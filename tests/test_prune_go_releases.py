import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy" / "prune-go-releases.sh"


def make_tree(tmp_path, stamps, envs=None, manifest=None, owner=None):
    root = tmp_path / "voice-go"
    for stamp in stamps:
        (root / "releases" / stamp).mkdir(parents=True)
    for stamp, text in (envs or {}).items():
        (root / "releases" / stamp / ".env").write_text(text)
    if manifest:
        update = root / "updates" / "20261025T000000Z"
        update.mkdir(parents=True)
        (update / "manifest").write_text(manifest)
    owner_file = tmp_path / "owner"
    if owner:
        owner_file.write_text(owner)
    return root, owner_file


def run(root, owner_file, *args):
    # PATH without docker: only the release layout, .env files, owner and manifests decide what is kept.
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={"PATH": "/usr/bin:/bin", "PRUNE_GO_ROOT": str(root), "PRUNE_OWNER_FILE": str(owner_file)},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def stamps(n):
    return [f"2026102{i}T000000Z" for i in range(n)]


def test_dry_run_deletes_nothing_and_keeps_the_newest(tmp_path):
    names = stamps(6)
    root, owner = make_tree(tmp_path, names)
    out = run(root, owner, "--keep", "2")

    assert "would remove 4 release(s)" in out
    assert sorted(p.name for p in (root / "releases").iterdir()) == sorted(names)


def test_a_release_named_by_the_env_of_a_kept_release_is_kept(tmp_path):
    # The 2026-10-24 incident: the live Go .env pointed VOICE_ARI_RUNTIME at an older release that was pruned.
    names = stamps(6)
    env = f"VOICE_ARI_RUNTIME={tmp_path}/voice-go/releases/{names[0]}/runtime\n"
    root, owner = make_tree(tmp_path, names, envs={names[5]: env})
    out = run(root, owner, "--keep", "1", "--apply")

    assert (root / "releases" / names[0]).is_dir()
    assert (root / "releases" / names[5]).is_dir()
    assert not (root / "releases" / names[2]).exists()
    assert f"removed {names[2]}" in out


def test_owner_and_rollback_manifest_releases_are_kept(tmp_path):
    names = stamps(6)
    manifest = f"go_release={tmp_path}/voice-go/releases/{names[1]}\nold_runtime={tmp_path}/voice-go/releases/{names[2]}/runtime\n"
    root, owner = make_tree(tmp_path, names, manifest=manifest, owner=names[0])
    run(root, owner, "--keep", "1", "--apply")

    survivors = sorted(p.name for p in (root / "releases").iterdir())
    assert survivors == sorted([names[0], names[1], names[2], names[5]])


def test_rejects_a_bad_keep_value(tmp_path):
    root, owner = make_tree(tmp_path, stamps(2))
    result = subprocess.run(
        ["bash", str(SCRIPT), "--keep", "x"],
        env={"PATH": "/usr/bin:/bin", "PRUNE_GO_ROOT": str(root), "PRUNE_OWNER_FILE": str(owner)},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
