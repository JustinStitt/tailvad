import re
from pydantic import BaseModel
from subprocess import check_output
from typing import override

TAILSCALE_BINARY = "tailscale"
IP_PATTERN = r"(?P<ip>(\d+)\.(\d+)\.(\d+)\.(\d+))"


class ExitNodeListEntry(BaseModel):
    ip: str
    hostname: str
    country: str
    city: str
    status: str


class ExitNodeSuggested(BaseModel):
    hostname: str


class ExitNodeActive(BaseModel):
    tailscale_ip: str
    visible_ip: str
    hostname: str

    @override
    def __repr__(self) -> str:
        return f"✦ [active] exit node {self.hostname} @ {self.visible_ip}"

    @override
    def __str__(self) -> str:
        return f"✦ active exit node - {self.hostname} @ {self.visible_ip}"


def get_tailscale_list_nodes(country_filter: str | None = None) -> list[ExitNodeListEntry]:
    """
    Example table data from $ tailscale exit-node list
            IP                  HOSTNAME                         COUNTRY            CITY                   STATUS
    100.111.189.27      al-tia-wg-003.mullvad.ts.net     Albania            Tirana                 -
    100.90.114.81       ar-bue-wg-001.mullvad.ts.net     Argentina          Buenos Aires           -
    """
    cmd = [TAILSCALE_BINARY, "exit-node", "list"]
    if country_filter:
        cmd.extend(["--filter", country_filter])
    output = check_output(cmd).decode()

    all_exit_nodes: list[ExitNodeListEntry] = []
    for line in output.split("\n"):
        if not re.search(pattern=IP_PATTERN, string=line):
            continue
        s = [x.strip() for x in line.split("  ") if len(x) > 0]
        entry = ExitNodeListEntry(
            ip=s[0], hostname=s[1], country=s[2], city=s[3], status=s[4]
        )
        all_exit_nodes.append(entry)
    return all_exit_nodes


def get_tailscale_current_exit_node() -> ExitNodeActive | None:
    """
    Example output for $ tailscale status --active
    00.02.010.04   omarchy-omnibook-aero-7       JustinStitt@    linux  -

    100.00.004.017  us-chi-wg-307.mullvad.ts.net  tagged-devices         active; exit node; direct 60.000.06.108
    :51820, tx 13808 rx 11800
    """
    cmd = [TAILSCALE_BINARY, "status", "--active"]
    output = check_output(cmd).decode()
    line = [x for x in output.split("\n") if "exit node;" in x]
    if len(line):
        line = line[0]
    else:
        return None
    s = [x.strip() for x in line.split("  ") if len(x) > 0]
    ip, hostname = s[0], s[1]
    m = re.search(pattern=IP_PATTERN, string=s[-1])
    if m:
        visible_ip: str = m.group("ip")
        return ExitNodeActive(tailscale_ip=ip, hostname=hostname, visible_ip=visible_ip)
    return None


def get_tailscale_suggested_exit_node() -> ExitNodeSuggested | None:
    """
    Example output for $ tailscale exit-node suggest
    Suggested exit node: us-chi-wg-307.mullvad.ts.net.
    To accept this suggestion, use `tailscale set --exit-node=us-chi-wg-307.mullvad.ts.net.`.
    """
    cmd = [TAILSCALE_BINARY, "exit-node", "suggest"]
    output = check_output(cmd).decode()
    line = [x for x in output.split("\n") if "Suggested exit node" in x]
    if len(line):
        line = line[0]
    else:
        return None

    s: str = line.split(":")[1].strip()
    if s.endswith("."):
        s = s[:-1]

    return ExitNodeSuggested(hostname=s)


def set_tailscale_exit_node(hostname: str) -> None:
    cmd = [TAILSCALE_BINARY, "set", f"--exit-node={hostname}"]
    _ = check_output(cmd).decode()


if __name__ == "__main__":
    # active = get_tailscale_current_exit_node()
    suggested = get_tailscale_suggested_exit_node()
    print(suggested)
