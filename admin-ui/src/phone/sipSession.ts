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

  constructor(
    private readonly signalingUrl: string,
    private readonly onRegistration: (status: SIPStatus) => void,
    private readonly onCall: (status: CallStatus, caller?: string) => void,
  ) {}

  async connect(credentials: SIPCredentials, audio: HTMLAudioElement): Promise<void> {
    this.audio = audio;
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
      delegate: { onInvite: (invitation) => this.receive(invitation) },
      sessionDescriptionHandlerFactoryOptions: {
        constraints: { audio: true, video: false },
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
      sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } },
    });
    this.attachCall(inviter);
    this.onCall("calling", extension);
    await inviter.invite();
  }

  async answer(): Promise<void> {
    if (!this.invite) return;
    await this.invite.accept({ sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } } });
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
    const audio = this.audio;
    if (!peerConnection || !audio) return;
    const stream = new MediaStream(peerConnection.getReceivers().map((receiver) => receiver.track).filter((track) => track.kind === "audio"));
    audio.srcObject = stream;
    void audio.play().catch(() => undefined);
  }

  private clearCall(): void {
    this.session = undefined;
    this.invite = undefined;
    if (this.audio) this.audio.srcObject = null;
    this.onCall("idle");
  }
}
