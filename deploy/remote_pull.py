"""
Auto-deploy: SSH into the home server, pull the latest code, restart the app.

Reads connection settings from deploy/remote.json (gitignored — it holds the
SSH credentials). Template: deploy/remote.example.json

Usage:
    python deploy/remote_pull.py            # pull + restart + verify
    python deploy/remote_pull.py --no-restart   # pull only (frontend changes)
    python deploy/remote_pull.py --check    # just test the connection
"""

import shlex
import sys
import json
from pathlib import Path

import paramiko

HERE = Path(__file__).resolve().parent
CFG_FILE = HERE / "remote.json"


def load_cfg() -> dict:
    if not CFG_FILE.exists():
        sys.exit(f"missing {CFG_FILE} — copy remote.example.json and fill it in")
    cfg = json.loads(CFG_FILE.read_text(encoding="utf-8"))
    for field in ("host", "user", "app_dir"):
        if not cfg.get(field):
            sys.exit(f"{CFG_FILE}: '{field}' is required")
    return cfg


def connect(cfg) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": cfg["host"],
        "port": int(cfg.get("port", 22)),
        "username": cfg["user"],
        "timeout": 15,
        "allow_agent": True,
        "look_for_keys": True,
    }
    key_file = cfg.get("key_file")
    if key_file:
        kwargs["key_filename"] = str(Path(key_file).expanduser())
    if cfg.get("password"):
        kwargs["password"] = cfg["password"]
    ssh.connect(**kwargs)
    return ssh


def run(ssh, cmd: str, sudo_pass: str = "", timeout: int = 180) -> tuple[int, str]:
    """Run a command; when sudo is needed and a password is configured, pipe
    it to `sudo -S`. Returns (exit_code, combined output)."""
    if cmd.startswith("sudo ") and sudo_pass:
        inner = shlex.quote(cmd)
        cmd = (f"printf %s {shlex.quote(sudo_pass + chr(10))} "
               f"| sudo -S -p '' bash -c {inner}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out.strip()


STEPS = [
    ("git pull", "pull latest code", False),
    ("sudo systemctl restart {service}", "restart service", True),
    ("sleep 2 && systemctl is-active {service}", "service status", False),
    ("curl -s -m 8 localhost:{port}/api/auth", "app responds", False),
    ("cd {app_dir} && git rev-parse --short HEAD", "running commit", False),
]


def main() -> None:
    cfg = load_cfg()
    restart = "--no-restart" not in sys.argv
    check_only = "--check" in sys.argv

    print(f"connecting to {cfg['user']}@{cfg['host']} ...")
    try:
        ssh = connect(cfg)
    except paramiko.AuthenticationException:
        sys.exit("auth failed — check user/password/key_file in remote.json")
    except Exception as e:                                 # noqa: BLE001
        sys.exit(f"connection failed: {e}")
    print("connected.\n")

    sudo_pass = cfg.get("sudo_password", "")
    fmt = {
        "app_dir": cfg["app_dir"],
        "service": cfg.get("service", "sd-agent"),
        "port": cfg.get("port_app", 8000),
    }
    failed = False
    try:
        if check_only:
            code, out = run(ssh, "echo ok && uname -n")
            print(f"  check: {out} (exit {code})")
            sys.exit(0 if code == 0 else 1)

        for tmpl, label, needs_restart in STEPS:
            if needs_restart and not restart:
                continue
            if needs_restart and not cfg.get("sudo_password"):
                print(f"  ! {label}: skipped (no sudo_password in remote.json)")
                continue
            code, out = run(ssh, tmpl.format(**fmt), sudo_pass)
            ok = code == 0
            failed |= not ok
            print(f"  {'✓' if ok else '✗'} {label}:")
            for line in out.splitlines()[-4:]:
                print(f"      {line}")
            if label == "app responds" and '"authed"' not in out:
                ok = False
                failed = True
                print("      (expected {\"authed\": false})")
        sys.exit(1 if failed else 0)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()