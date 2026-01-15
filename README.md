# AFTERIMAGE

> *"The only way to keep a secret is to never have one."*  
> — But what if the medium itself forgets?

---

## The Problem

They watch the wires. They own the spectrum. Every packet you send traverses infrastructure you do not control, logged by entities you will never meet, parsed by algorithms you cannot audit.

TCP/IP was a dream of openness. It became a panopticon.

Bluetooth whispers, but its handshake screams. RF is a broadcast to anyone with an ear. Even "end-to-end encryption" relies on *their* relays, *their* timestamps, *their* metadata.

The network is compromised. Not by a bug, but by design. The architecture itself is the vulnerability.

---

## The Thesis

**Light leaves no trace.**

A photon, once absorbed, carries no history. There is no log. There is no handshake. There is no "connection" to monitor. The transmission exists only in the moment it is observed.

AFTERIMAGE weaponizes this principle. It is not a network protocol. It is an *optical exfiltration channel*—a one-way bridge from a sealed system to the outside world, using the most ancient and uncontrollable medium: *visible light*.

---

## The Philosophy

We believe:

1.  **Air-gaps are the only trust boundary.** Software can be backdoored. Hardware can be implanted. But a machine that *never connects* can only be compromised by physical presence. AFTERIMAGE extends this trust across a visual bridge.

2.  **Unidirectionality is a feature, not a limitation.** A channel that cannot receive commands cannot be exploited remotely. The receiver is passive. The transmitter is sovereign.

3.  **Data should be ephemeral by default.** The QR stream exists only on a screen, for a moment. Once the transmission ends, the channel vanishes. There is no session to hijack, no socket to probe.

4.  **Resilience is non-negotiable.** In a hostile environment, frames will be lost. Cameras will shake. Obstructions will occur. AFTERIMAGE uses *Fountain Codes*—a mathematical structure where the data "floats" above the noise. Any sufficient subset of received fragments reconstructs the whole. Order is irrelevant. Loss is tolerated. The signal survives.

---

## The Name

An **afterimage** is the visual imprint that persists on your retina after a bright light is removed. It is a phantom—something you *know* you saw, but cannot prove.

This tool creates data afterimages. A file, once transmitted, leaves no fingerprint on the sender's system beyond the original file itself. The stream is gone. The QR codes dissolve. What remains is only the *memory* of light, captured by a camera that was never on the network.

---

## The Cryptographic Guarantee

We do not trust the optical channel. We trust only the mathematics.

Before a single photon leaves the screen, your data is:

1.  **Compressed** (zlib) - To minimize transmission time.
2.  **Encrypted** (ChaCha20-Poly1305) - Authenticated encryption. If a single bit is tampered with, decryption fails entirely.
3.  **Encoded** (LT Fountain Codes) - Rateless erasure coding. The receiver needs only *enough* droplets, not *all* of them.

The password never leaves your mind. The key is derived locally, ephemerally, and discarded after use. There is no key exchange. There is no certificate authority. There is no third party.

If the adversary captures the entire QR stream, they possess only *encrypted noise*. The file does not exist without the key. The key does not exist without you.

---

## The Scenario

You are in a facility. The machine before you has never touched a network. It contains something that must leave.

Plug in a USB stick? They log those. Every bit, every hash, every timestamp.  
Connect to WiFi? There is no WiFi. This machine was never meant to speak.  
Print it? Paper is controlled. Serial numbers, invisible watermarks.

But there is a window. And outside that window, there is a camera.

You run AFTERIMAGE. The screen flickers with QR codes—hundreds, thousands—a torrent of structured noise. Outside, a phone records. It misses frames. It shakes in the wind. It captures perhaps 60% of what was shown.

It is enough.

The file reconstructs. The channel closes. The room is as it was.

There is no log. There is no metadata. There is only the afterimage.

---

## Final Thoughts

We do not build tools for the lawful. We do not build tools for the lawless. We build tools for the *autonomous*—those who believe that the ability to communicate privately is not a privilege granted by the state, but a natural right that predates the state.

The cypherpunks wrote code because code is law. Regulation is temporary; mathematics is eternal. AFTERIMAGE is a small piece of that larger project: the creation of systems that empower individuals against institutions, the weak against the powerful, the many against the few.

Use it wisely. Or don't. The code doesn't care. It only runs.

---

*"Privacy is necessary for an open society in the electronic age. Privacy is not secrecy. A private matter is something one doesn't want the whole world to know, but a secret matter is something one doesn't want anybody to know. Privacy is the power to selectively reveal oneself to the world."*

— Eric Hughes, *A Cypherpunk's Manifesto*, 1993

---

**AFTERIMAGE**  
*When the screen goes dark, the data survives.*
