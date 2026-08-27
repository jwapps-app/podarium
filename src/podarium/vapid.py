"""Generate a VAPID keypair: `python -m podarium.vapid`.

Run once, put the two values in the environment, and leave them alone. Rotating the private
key invalidates every subscription already issued against the public one -- browsers pin
the key at subscribe time -- so a rotation means every device has to re-enable
notifications.
"""

import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02


def public_key_b64(vapid: Vapid02) -> str:
    """The public key as the browser wants it: raw uncompressed point, base64url, unpadded.

    Not the PEM. `applicationServerKey` is fed straight into PushManager.subscribe as bytes,
    and a PEM there fails with an error that says nothing about why.
    """
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")



def main() -> None:
    vapid = Vapid02()
    vapid.generate_keys()

    private_pem = vapid.private_pem().strip()
    # Printed base64-encoded rather than as a PEM. Both are accepted, but this value has to
    # survive a Portainer environment panel, and a multi-line PEM full of newlines and
    # slashes is exactly the kind of thing that arrives truncated.
    private = base64.b64encode(private_pem).decode()

    print("Paste these three into the environment. Push stays off until they are set.")
    print()
    print("VAPID_PUBLIC_KEY=" + public_key_b64(vapid))
    print("VAPID_PRIVATE_KEY=" + private)
    print("VAPID_CONTACT=mailto:you@example.com")
    print()
    print("Keep the private key. Browsers pin the public one when a device subscribes, so")
    print("replacing this pair means every device has to turn notifications on again.")


if __name__ == "__main__":
    main()
