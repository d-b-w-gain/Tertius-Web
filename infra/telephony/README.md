# Spruik

This deployment runs Asterisk on the k3s node's host network so SIP and RTP are
available directly at `192.168.88.29`. It is intentionally private to the home
network; no router port-forward is required.

The PBX registers once with Aussie Broadband and rings two internal endpoints:

- `101`: office PC
- `102`: Daniel's iPhone

The first endpoint to answer wins. Authentication is stored in the Kubernetes
Secret `telephony/asterisk-secrets`; no credentials belong in this directory.

Incoming calls are answered by Asterisk, receive a short Kokoro-generated
welcome announcement, and then ring both endpoints while a Kokoro-generated
summary of Gain Engineering's services plays to the caller. The hold message
stops as soon as an endpoint answers and loops if it reaches the end. The
Asterisk init container generates the 24 kHz signed-linear audio from the in-cluster
`kokoro-tts.tts.svc` API whenever the PBX pod starts. This makes a failed or
unavailable TTS service visible during deployment instead of silently answering
calls without an announcement.

For first-time setup, copy the plain upstream SIP password and run
`scripts/initialize-telephony-secrets.ps1 -UpstreamUsername <username>`. The
script uses the clipboard value without displaying it, generates internal
extension passwords, creates the Kubernetes Secret, and writes the endpoint
credentials under `%LOCALAPPDATA%\Spruik`.

Deploy or update Asterisk with `scripts/deploy-telephony.ps1`. It requires the
Secret to exist and deliberately preserves it. Configure the PC by running
`scripts/configure-microsip-pbx.ps1 -StartAfterUpdate`; the direct carrier
account is backed up before extension `101` replaces it.

For the initial test, configure both endpoints against `192.168.88.29` over UDP
port `5060`. A later Tailscale phase can expose the same private service without
opening SIP to the public internet.

Dial `600` from either endpoint to preview the welcome announcement. To place a
server-generated test call that plays the announcement after it is answered,
run `scripts/test-telephony-announcement.ps1`; extension `102` is the default.
Dial `601` to preview the promotional message callers hear while the endpoints
ring. Dial `602` to exercise the complete voicemail recording and Signal
delivery path without making an external call.

## Spruik manager

The live Asterisk pod includes the token-protected Spruik manager on
`http://192.168.88.29:8088`. Its admin token lives in the
`telephony/spruik-manager` Secret and in the current Windows user's private
`%LOCALAPPDATA%\Spruik\admin-token.txt` file. The token is entered into the page
and kept only in browser memory.

The manager shows sanitised registration and endpoint status, edits and previews
the standard, promotional, voicemail, and confirmation prompts, starts internal
test calls, and exposes only retained voicemail files. Prompt settings survive
pod restarts on the `spruik-data` persistent volume.

Calls from Johnson Yuen (`0406426654` or its Australian international form) use
a personalized path. Asterisk sends ringing first, asks Kokoro to generate a
Sydney-time-aware morning, afternoon, or evening greeting, then answers and
plays it before ringing extensions `101` and `102`. If live generation fails,
the standard pre-generated greeting is used rather than dropping the call.

The same pre-answer path recognizes Dan (`0408196458`) and Mike
(`0403801695`), including their `61` international forms. Each profile supplies
its own script and Kokoro voice; Mike uses `am_santa`. Phone numbers are
normalized to digits before matching, so an incoming leading `+` is harmless.

Hayley Scrivenor (`0424447336`, including its `61` international form) has a
warm literary greeting about Mausguard, generated with `bf_lily` before the
call is answered.

## Signal voicemail

Before announcing that a call will be connected, Asterisk checks for live PJSIP
contacts on extensions `101` and `102`. If neither endpoint is registered, it
answers with a Kokoro-generated voicemail prompt, records up to two minutes,
and sends the WAV recording to the linked Signal account's Note to Self chat.
An unanswered call also falls through to the same voicemail path after the
35-second ring timeout. If every available endpoint declines, reports busy, or
becomes unavailable before then, voicemail starts as soon as the final Dial
attempt ends.

The Signal account number is read from the `telephony/voicemail-signal` Secret;
it is never stored in this repository. Successfully delivered recordings are
deleted. Failed deliveries remain on the `asterisk-voicemail` persistent volume
for recovery rather than being silently discarded.

## Background iPhone audio experiment

The same namespace contains a private Mumble 1.5 server on the k3s node's host
network at `192.168.88.29:64738` (TCP and UDP). This is the non-SIP iPhone audio
endpoint: Wumble can keep an encrypted Mumble audio session active while the
phone is locked, while Asterisk remains responsible only for the carrier line.

The `mumble-announcer` bot connects to the root channel and exposes an
in-cluster `/announce` endpoint. It converts Kokoro's 24 kHz raw PCM output to
the 48 kHz PCM expected by Mumble and injects it into the channel. This provides
a safe receive-audio and lock-screen test before incoming phone calls are
bridged into Mumble.

Both the Mumble server password and its administrator password live only in the
`telephony/mumble-secrets` Secret. Do not commit either credential. Wumble needs
the node address, port, any unique display name, and the server password; it
does not need the administrator password.
