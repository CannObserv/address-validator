# Tailnet

How this host reaches the sibling [notifier](https://github.com/CannObserv/notifier)
service (#232). exe.dev VMs share no private network, and
`https://notifier.exe.xyz` stops at the exe.dev login gate. The
`cannobserv.org.github` tailnet is the only route. Notifier's own view of the
tailnet is in its `docs/reference/tailscale.md`.

## This node

| | |
|---|---|
| Node | `address-validator`, `100.75.8.39`, MagicDNS `address-validator.taild0fb76.ts.net` |
| Tag | `tag:address-validator`: tagged, **non-ephemeral**, key expiry disabled |
| ACL | `tag:address-validator → tag:notifier:9000` only. `:9001` (notifier dev) is dropped |
| Joined | 2026-09-29, Tailscale 1.102.4 from the official apt repo |
| DNS | MagicDNS owns `/etc/resolv.conf` (operator decision): every outbound name, USPS and Google included, resolves through `tailscaled` |

`tailscaled` is roughly 51 MB RSS, and on this swapless host it is a
page-allocation-failure casualty. See [HOST-MEMORY.md](HOST-MEMORY.md).

## Check

```bash
tailscale status                        # notifier listed
curl -s http://notifier:9000/health     # "environment": "production"
```

A missing ACL rule looks like DNS failure (`Could not resolve host: notifier`),
not like a refused connection: without the rule, notifier is absent from this
node's netmap.

## Join or rejoin

The operator generates a **single-tag, pre-approved, non-ephemeral** auth key
for `tag:address-validator` in the admin console. A key scoped to several tags
applies all of them.

```bash
# Tailscale apt repo (Ubuntu noble), then the package
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
sudo apt-get update && sudo apt-get install -y tailscale

# Stage the key in a 0600 file: never in argv, which PAM audits into journald.
sudo install -m 600 /dev/null /run/ts.key
sudo tee /run/ts.key >/dev/null            # paste the key, then Ctrl-D
sudo systemctl enable --now tailscaled
sudo tailscale up --auth-key=file:/run/ts.key --hostname=address-validator
sudo shred -u /run/ts.key
```

**Tags bind at first registration.** Re-authenticating with a differently
tagged key does not retag the node. Changing tags takes `tailscale logout` and
a fresh `tailscale up`, and while that happens the notifier hop is down. Get
the tag right before the first join.

If the node's address changes on a rejoin, update the row above and notifier's
`docs/reference/tailscale.md`. The ACL matches the tag, not the address.
