# Edge — a desk window to your mind

Edge is a small always-on widget on a desk display (first target: Corsair iCUE
dashboard widgets, for example the Xeneon Edge). It shows your main conversation,
streams replies, and shows what the mind says on its own (morning, heartbeat,
reflections). The widget is open source:
[avasol/xeneon-edge-companion](https://github.com/avasol/xeneon-edge-companion);
its contract is that repo's `PROTOCOL.md` (Edge Protocol v1.1). This engine is one
server for it.

| New to this | I use keys daily | How and why it works |
|---|---|---|
| 1. Start the engine as usual. | `curl -s -X POST http://127.0.0.1:8080/api/edge-token` prints a new token once. | The token is 32 random bytes, kept in `<data root>/edge_token` with mode 0600, or in `EDGE_TOKEN` if you set it (then it can only be changed there). |
| 2. Ask for a token (the command on the right), and copy what it prints. It is shown only once. | Asking again replaces it; the old token and every file link made with it stop working at once. | The token is compared in constant time and accepted only in the `X-Edge-Token` header, never in a URL, so it does not end up in logs or browser history. |
| 3. Install the widget file from the widget repo's releases in iCUE. | Build your own with `python build.py --skin <id>` in the widget repo. | The widget is a web page served by iCUE from another origin. Requests under `/api/edge/` may cross origins only with a valid token; every other route keeps the Tower's host and origin gates. |
| 4. In the widget's settings, paste the Tower address (`http://127.0.0.1:8080`) and the token. | Another machine: tunnel to the Tower and add the name to `TOWER_ALLOWED_HOSTS`. | The Tower listens on this machine only. Same machine is as safe as it can be made without a login; across a network, use your own tunnel. |
| 5. Talk. Morning and other messages from the mind show up on their own. | `GET /api/edge/hello` lists what this server offers: `chat`, `stream`, `push`, `new`. | Scheduled messages are handed to every registered sink as well as Discord, so Edge receives them even with no Discord bot. A broken sink never stops delivery. |

## What this server offers

| Route | Purpose |
|---|---|
| `GET /api/edge/hello` | protocol, features |
| `GET /api/edge/history` | the last 40 messages of the main conversation (tool calls hidden) |
| `POST /api/edge/stream` | send a message; server-sent events: `turn`, `done` or `error`, keepalives |
| `GET /api/edge/poll` | the next message from the mind, plus finished turns not yet acknowledged |
| `POST /api/edge/turn/<id>/ack` | the widget has shown turn `<id>` |
| `POST /api/edge/new` | start a fresh conversation |
| `POST /api/edge/trace` | the widget's own event log (bounded, plain JSON) into `memory/edge_trace/` |
| `GET /api/edge/file/<signed>` | one file from `memory/media/`, by a link that expires in 24 hours |

Not offered here: approval cards (this engine asks for destructive commands on the
console or in Discord) and attachments from the widget. The widget hides panels a
server does not list in `hello`.

**Turn recovery.** Every finished turn is reported in `poll` until the widget
acknowledges it. A widget that lost its stream reloads the history first, swaps it in
only if that worked, and acknowledges only after it has shown the reply. A dropped
connection costs a reload, never a reply.
