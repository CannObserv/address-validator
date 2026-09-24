"""Drift guard: earlyoom's written configuration, and the host class it assumes (GH #225).

Sessions here inherit ``oom_score_adj`` -1000 from exe.dev's ``sshd``, and
earlyoom 1.7 skips a -1000 process exactly as the kernel does (``kill.c:242-253``),
``--prefer`` or not. So earlyoom can never take a session on this host; what it
can do is shed the host's own processes before the kernel stalls. A dry run on
2026-09-24 showed stock earlyoom taking libpostal first, the process
``docs/HOST-MEMORY.md`` ranks first to lose, but postgres fifth. So it is kept,
and ``infra/earlyoom.default`` sets the order with ``--prefer``/``--avoid``
rather than leaving it to RSS.

Four ways the file silently regresses, guarded here:

- a value gains a space, or a regex a backslash. Debian's unit expands
  ``$EARLYOOM_ARGS`` unquoted, so a space splits the value into a stray
  argument (earlyoom 1.7 then exits 13 and nothing runs) and a backslash is
  dropped with no error at all;
- ``--prefer`` stops matching libpostal's comm, which the kernel truncates to 15
  characters (a ``$``-anchored full binary name matches nothing);
- ``--avoid`` stops covering the service, postgres or the user manager, or a
  process matches both regexes;
- ``-s 100,100`` goes, so swap added later makes earlyoom wait for it to drain.

Two checks read host state, so they run only on the host and skip elsewhere:

- the running daemon carries this file's arguments. ``apt install`` starts it on
  stock ``-r 3600``, and ``enable --now`` never restarts it, so a unit that
  reads active can be running something else;
- this session's root still sits at -1000. Nothing in this repo sets that. If
  exe.dev's inheritance changes, sessions become killable, the premise above is
  wrong, and #225 needs revisiting.
"""

import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "infra" / "earlyoom.default"
SERVICE_UNIT = REPO_ROOT / "infra" / "address-validator.service"
INSTALLED = Path("/etc/default/earlyoom")
HOST = "address-validator"

on_the_host = pytest.mark.skipif(socket.gethostname() != HOST, reason=f"not {HOST}")

# earlyoom's regexes match /proc/<pid>/comm, which the kernel truncates to 15 chars.
COMM_LEN = 15
LIBPOSTAL_COMM = "wof-libpostal-s"  # pelias/libpostal-service's wof-libpostal-server

# Userspace comms on the host, 2026-09-24: every /proc/<pid>/comm with a non-zero
# VmRSS (kernel threads have none), less one-off commands (sleep, sort, tr).
HOST_COMMS = frozenset(
    {
        "(sd-pam)",
        "MainThread",
        "bash",
        "claude",
        "containerd",
        "containerd-shim",
        "cron",
        "dbus-daemon",
        "docker",
        "docker-proxy",
        "dockerd",
        "earlyoom",
        "npm exec socrat",
        "ollama",
        "polkitd",
        "postgres",
        "qdrant",
        "sh",
        "sshd",
        "sshd-session",
        "systemd",
        "systemd-journal",
        "systemd-logind",
        "systemd-timesyn",
        "uvicorn",
        LIBPOSTAL_COMM,
    }
)

OOM_FLOOR = -1000
# What exe.dev starts a session from; the -1000 is theirs, inherited or not.
SESSION_PARENTS = frozenset({"exe-init", "sshd"})


def _value() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.startswith("EARLYOOM_ARGS=")]
    assert len(lines) == 1, f"{CONFIG.name} must set EARLYOOM_ARGS exactly once, found {len(lines)}"
    m = re.fullmatch(r'EARLYOOM_ARGS="([^"]*)"', lines[0])
    assert m, f"{CONFIG.name}: EARLYOOM_ARGS is not one double-quoted value: {lines[0]!r}"
    return m.group(1)


def _args() -> list[str]:
    # What systemd hands earlyoom: EnvironmentFile= strips the double quotes, and
    # `ExecStart=/usr/bin/earlyoom $EARLYOOM_ARGS` splits the expansion on whitespace.
    return _value().split()


def _flag(name: str) -> str:
    args = _args()
    assert args.count(name) == 1, f"{CONFIG.name} must pass {name} exactly once"
    return args[args.index(name) + 1]


def _service_comm() -> str:
    unit = SERVICE_UNIT.read_text(encoding="utf-8")
    exec_start = re.search(r"^ExecStart=(\S+)", unit, re.MULTILINE)
    assert exec_start, f"{SERVICE_UNIT.name} has no ExecStart="
    return Path(exec_start.group(1)).name[:COMM_LEN]


def test_every_regex_reaches_earlyoom_as_one_argument() -> None:
    stray = sorted({c for c in _value() if c in "\\'"})
    assert not stray, (
        f"{CONFIG.name} contains {stray}: systemd splits $EARLYOOM_ARGS itself, so a "
        "backslash is dropped (\\. becomes ., matching anything) and quotes are not "
        "the shell's. Keep regexes free of spaces and escapes; a literal is [.]"
    )


# Each flag this file passes takes exactly one value.
FLAGS = frozenset({"-r", "-m", "-s", "--prefer", "--avoid"})
PERCENT_PAIR = re.compile(r"(\d+),(\d+)")


def test_every_argument_is_a_flag_and_its_value() -> None:
    # A space splits a value into a stray token; earlyoom 1.7 then exits 13
    # ("extra argument not understood") and the host has no early killer at all.
    args = _args()
    flags = args[0::2]
    assert len(args) % 2 == 0 and set(flags) <= FLAGS, (
        f"{CONFIG.name} does not read as flag/value pairs over {sorted(FLAGS)}: {args}. "
        "A space inside a value becomes a second argument, and earlyoom refuses to start"
    )
    for name in ("-m", "-s"):
        m = PERCENT_PAIR.fullmatch(_flag(name))
        assert m and int(m.group(2)) <= int(m.group(1)), (
            f"{name} {_flag(name)!r} is not PERCENT,KILL_PERCENT with KILL <= PERCENT"
        )
    for name in ("--prefer", "--avoid"):
        re.compile(_flag(name))


def test_memory_alone_decides() -> None:
    assert _flag("-s") == "100,100", (
        "earlyoom acts only when memory AND swap are both low; without -s 100,100 "
        "a swap added later makes it wait until swap is ~90% used (-s 100 alone "
        "still gates SIGKILL on half of it)"
    )


@pytest.mark.parametrize("comm", [LIBPOSTAL_COMM, "qdrant", "ollama"])
def test_what_restarts_on_its_own_is_preferred(comm: str) -> None:
    assert re.search(_flag("--prefer"), comm), (
        f"--prefer does not match {comm!r}. libpostal is what HOST-MEMORY.md loses "
        "first, and SocratiCode's qdrant and ollama restart on their own. The match is "
        "against the 15-char comm, so never $-anchor a full binary name"
    )


@pytest.mark.parametrize("comm", [_service_comm(), "postgres", "systemd", "(sd-pam)"])
def test_the_service_its_database_and_the_user_manager_are_avoided(comm: str) -> None:
    assert re.search(_flag("--avoid"), comm), (
        f"--avoid does not match {comm!r}. Unavoided, stock ordering puts postgres "
        "fifth (a 503) and the user manager third, and `systemd-run --user` caps need it"
    )


def test_no_process_is_both_preferred_and_avoided() -> None:
    prefer, avoid = _flag("--prefer"), _flag("--avoid")
    comms = HOST_COMMS | {_service_comm()}
    both = sorted(c for c in comms if re.search(prefer, c) and re.search(avoid, c))
    assert not both, f"{both} match both --prefer and --avoid; the two ±300s cancel"


def _session_root_adj(proc: Path, pid: int) -> int | None:
    """``oom_score_adj`` of the process exe.dev started ``pid``'s session from.

    Walks the ancestry to the first process whose parent is in
    ``SESSION_PARENTS`` and reads that one, not ``pid`` itself: a leaf can be
    ``choom``'d (HOST-MEMORY.md's capped install is), the session root cannot.
    ``None`` when no ancestor is a session: CI, a systemd unit, cron.
    """
    while pid > 1:
        status = (proc / str(pid) / "status").read_text()
        ppid = int(re.search(r"^PPid:\s*(\d+)", status, flags=re.MULTILINE).group(1))
        if ppid < 1:
            return None
        if (proc / str(ppid) / "comm").read_text().strip() in SESSION_PARENTS:
            return int((proc / str(pid) / "oom_score_adj").read_text())
        pid = ppid
    return None


def _fake_process(proc: Path, pid: int, ppid: int, comm: str, adj: int) -> None:
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "status").write_text(f"Name:\t{comm}\nPPid:\t{ppid}\n")
    (proc / str(pid) / "comm").write_text(f"{comm}\n")
    (proc / str(pid) / "oom_score_adj").write_text(f"{adj}\n")


def test_a_session_under_sshd_reports_its_root(tmp_path: Path) -> None:
    _fake_process(tmp_path, 245, 1, "sshd", -1000)
    _fake_process(tmp_path, 800, 245, "sshd-session", -1000)
    _fake_process(tmp_path, 900, 800, "npm", 500)  # a choom'd leaf
    assert _session_root_adj(tmp_path, 900) == -1000


def test_no_session_ancestor_is_none(tmp_path: Path) -> None:
    _fake_process(tmp_path, 1, 0, "systemd", 0)
    _fake_process(tmp_path, 300, 1, "systemd", 100)
    _fake_process(tmp_path, 301, 300, "python3", 0)
    assert _session_root_adj(tmp_path, 301) is None


@on_the_host
def test_the_running_daemon_has_this_files_arguments() -> None:
    assert INSTALLED.read_text(encoding="utf-8") == CONFIG.read_text(encoding="utf-8"), (
        f"{INSTALLED} differs from infra/{CONFIG.name}: "
        f"sudo cp infra/{CONFIG.name} {INSTALLED} && sudo systemctl restart earlyoom"
    )
    pid = subprocess.run(
        ["systemctl", "show", "earlyoom", "-p", "MainPID", "--value"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert pid not in ("", "0"), "earlyoom is not running: sudo systemctl restart earlyoom"
    argv = [a.decode() for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]]
    assert argv[1:] == _args(), (
        f"earlyoom runs {argv[1:]}, not infra/{CONFIG.name}'s arguments. "
        "`enable --now` never restarts an active unit: sudo systemctl restart earlyoom"
    )


@on_the_host
def test_sessions_here_are_still_exempt() -> None:
    adj = _session_root_adj(Path("/proc"), os.getpid())
    if adj is None:
        pytest.skip("not run from an exe.dev session")
    assert adj == OOM_FLOOR, (
        f"this session's root reads oom_score_adj={adj}, not {OOM_FLOOR}: exe.dev no "
        "longer exempts sessions here, so a killer can now take one. HOST-MEMORY.md's "
        "premise is wrong and #225's keep decision needs revisiting — at 0, upstream "
        "init-socraticode host-memory.md §4 --prefers the session's own processes"
    )
