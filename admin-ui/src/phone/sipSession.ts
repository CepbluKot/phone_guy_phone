import {
  Invitation,
  Inviter,
  Registerer,
  RegistererState,
  Session,
  SessionState,
  UserAgent,
} from "sip.js";
import type { SIPCredentials } from "./api";

export type SIPStatus = "connecting" | "registered" | "offline";
export type CallStatus = "idle" | "calling" | "ringing" | "connected";

export class BrowserSIPSession {
  private userAgent?: UserAgent;
  private registerer?: Registerer;
  private session?: Session;
  private invite?: Invitation;
  private audio?: HTMLAudioElement;
  private peerConnection?: RTCPeerConnection;
  private remoteTrackListener?: (event: RTCTrackEvent) => void;
  private remoteStream?: MediaStream;
  private inputDeviceId = "";
  private localInputStream?: MediaStream;
  private microphoneMuted = false;

  constructor(
    private readonly signalingUrl: string,
    private readonly onRegistration: (status: SIPStatus) => void,
    private readonly onCall: (status: CallStatus, caller?: string) => void,
    private readonly onAudioError: (message: string) => void = () => undefined,
  ) {}

  async connect(credentials: SIPCredentials, audio: HTMLAudioElement): Promise<void> {
    this.audio = audio;
    let initialConnection = true;
    const uri = UserAgent.makeURI(credentials.uri);
    if (!uri) throw new Error("invalid_sip_uri");
    const userAgent = new UserAgent({
      uri,
      transportOptions: { server: this.signalingUrl },
      authorizationUsername: credentials.username,
      authorizationPassword: credentials.password,
      displayName: credentials.username,
      logBuiltinEnabled: false,
      logConfiguration: false,
      // Asterisk is restarted during deployment. SIP.js does not reconnect by default.
      reconnectionAttempts: 1000000,
      reconnectionDelay: 4,
      delegate: {
        onInvite: (invitation) => this.receive(invitation),
        onDisconnect: () => this.onRegistration("offline"),
        onConnect: () => {
          if (initialConnection) {
            initialConnection = false;
            return;
          }
          this.onRegistration("connecting");
          void this.registerer?.register().catch(() => this.onRegistration("offline"));
        },
      },
      sessionDescriptionHandlerFactoryOptions: {
        constraints: this.mediaConstraints(),
      },
    });
    this.userAgent = userAgent;
    this.registerer = new Registerer(userAgent, { expires: 120 });
    this.registerer.stateChange.addListener((state) => {
      this.onRegistration(state === RegistererState.Registered ? "registered" : "offline");
    });
    this.onRegistration("connecting");
    await userAgent.start();
    await this.registerer.register();
  }

  async call(extension: string): Promise<void> {
    const userAgent = this.userAgent;
    if (!userAgent || !/^\d{1,16}$/.test(extension)) throw new Error("invalid_call_target");
    const host = new URL(this.signalingUrl).hostname;
    const target = UserAgent.makeURI(`sip:${extension}@${host}`);
    if (!target) throw new Error("invalid_call_target");
    const inviter = new Inviter(userAgent, target, {
      sessionDescriptionHandlerOptions: { constraints: this.mediaConstraints() },
    });
    this.attachCall(inviter);
    this.onCall("calling", extension);
    await inviter.invite();
  }

  async answer(): Promise<void> {
    if (!this.invite) return;
    await this.invite.accept({ sessionDescriptionHandlerOptions: { constraints: this.mediaConstraints() } });
  }

  async setInputDevice(deviceId: string): Promise<void> {
    const connection = this.peerConnection;
    const senders = connection?.getSenders().filter((sender) => sender.track?.kind === "audio") ?? [];
    if (!senders.length) {
      this.inputDeviceId = deviceId;
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: this.audioConstraint(deviceId), video: false });
    const track = stream.getAudioTracks()[0];
    if (!track) {
      stream.getTracks().forEach((mediaTrack) => mediaTrack.stop());
      throw new Error("audio_input_unavailable");
    }
    track.enabled = !this.microphoneMuted;
    const previousTracks = senders.map((sender) => sender.track).filter((previous): previous is MediaStreamTrack => !!previous);
    try {
      await Promise.all(senders.map((sender) => sender.replaceTrack(track)));
    } catch {
      stream.getTracks().forEach((mediaTrack) => mediaTrack.stop());
      throw new Error("audio_input_switch_failed");
    }
    previousTracks.forEach((previous) => previous.stop());
    this.localInputStream?.getTracks().forEach((previous) => previous.stop());
    this.localInputStream = stream;
    this.inputDeviceId = deviceId;
  }

  setMicrophoneMuted(muted: boolean): void {
    this.microphoneMuted = muted;
    const senders = this.peerConnection?.getSenders().filter((sender) => sender.track?.kind === "audio") ?? [];
    for (const sender of senders) {
      if (sender.track) sender.track.enabled = !muted;
    }
  }

  async decline(): Promise<void> {
    if (this.invite) await this.invite.reject();
    this.clearCall();
  }

  async hangup(): Promise<void> {
    const active = this.session;
    if (!active) return;
    if (active.state === SessionState.Established) await active.bye();
    else if (active instanceof Inviter) await active.cancel();
    else if (active instanceof Invitation) await active.reject();
    this.clearCall();
  }

  async resumeAudio(): Promise<void> {
    if (!this.audio?.srcObject) return;
    try {
      await this.audio.play();
      this.onAudioError("");
    } catch {
      this.onAudioError("Не удалось включить звук. Проверьте устройство вывода и разрешение на воспроизведение.");
    }
  }

  async disconnect(): Promise<void> {
    const registerer = this.registerer;
    const userAgent = this.userAgent;
    this.registerer = undefined;
    this.userAgent = undefined;
    if (this.session) await this.hangup().catch(() => undefined);
    if (registerer) await registerer.unregister().catch(() => undefined);
    if (userAgent) await userAgent.stop().catch(() => undefined);
    this.onRegistration("offline");
  }

  private audioConstraint(deviceId = this.inputDeviceId): true | MediaTrackConstraints {
    return deviceId ? { deviceId: { exact: deviceId } } : true;
  }

  private mediaConstraints(): MediaStreamConstraints {
    return { audio: this.audioConstraint(), video: false };
  }

  private receive(invitation: Invitation): void {
    if (this.session) {
      void invitation.reject();
      return;
    }
    this.invite = invitation;
    this.attachCall(invitation);
    this.onCall("ringing", invitation.remoteIdentity.uri.user ?? "");
  }

  private attachCall(session: Session): void {
    this.session = session;
    session.stateChange.addListener((state) => {
      if (state === SessionState.Established) {
        this.playRemoteAudio(session);
        this.onCall("connected");
      } else if (state === SessionState.Terminated) {
        this.clearCall();
      }
    });
  }

  private playRemoteAudio(session: Session): void {
    const handler = session.sessionDescriptionHandler as { peerConnection?: RTCPeerConnection } | undefined;
    const peerConnection = handler?.peerConnection;
    if (!peerConnection || !this.audio) return;
    this.peerConnection = peerConnection;
    this.remoteStream = new MediaStream();
    this.remoteTrackListener = (event) => {
      if (event.track.kind !== "audio" || !this.remoteStream) return;
      if (!this.remoteStream.getTracks().some((track) => track.id === event.track.id)) {
        this.remoteStream.addTrack(event.track);
      }
      void this.resumeAudioWithStream();
    };
    peerConnection.addEventListener("track", this.remoteTrackListener);
    for (const receiver of peerConnection.getReceivers()) {
      if (receiver.track.kind === "audio" && receiver.track.readyState !== "ended") {
        this.remoteStream.addTrack(receiver.track);
      }
    }
    if (this.remoteStream.getAudioTracks().length > 0) void this.resumeAudioWithStream();
  }

  private async resumeAudioWithStream(): Promise<void> {
    const audio = this.audio;
    if (!audio || !this.remoteStream) return;
    audio.srcObject = this.remoteStream;
    await this.resumeAudio();
  }

  private clearCall(): void {
    if (this.peerConnection && this.remoteTrackListener) {
      this.peerConnection.removeEventListener("track", this.remoteTrackListener);
    }
    this.peerConnection = undefined;
    this.remoteTrackListener = undefined;
    this.remoteStream = undefined;
    this.microphoneMuted = false;
    this.localInputStream?.getTracks().forEach((track) => track.stop());
    this.localInputStream = undefined;
    this.session = undefined;
    this.invite = undefined;
    if (this.audio) this.audio.srcObject = null;
    this.onCall("idle");
  }
}
