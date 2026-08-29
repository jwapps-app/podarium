import { api } from "./api";

/** The number on the home-screen icon.
 *
 *  Two things keep it honest, and they are different jobs. The service worker sets it from
 *  a push while the app is closed, which is the only way it can change without the app
 *  running -- iOS gives a web app no background execution, so between pushes the icon
 *  shows whatever the last one said. This module handles the other half: correcting it
 *  whenever the app is actually open, and clearing it when the inbox is looked at.
 *
 *  Every call is guarded and every failure swallowed. Badging is unsupported in most
 *  browsers, and on iOS it exists only for an app installed to the home screen and only
 *  once notifications have been allowed -- so the common case is that none of this does
 *  anything, and it must not be an error when it doesn't.
 */

function supported(): boolean {
  return typeof navigator !== "undefined" && "setAppBadge" in navigator;
}

async function paint(count: number): Promise<void> {
  if (!supported()) return;
  try {
    if (count > 0) await navigator.setAppBadge(count);
    else await navigator.clearAppBadge();
  } catch {
    // Permission not granted, or not an installed app. Nothing to do and nothing to say.
  }
}

/** Ask the server what the badge should be, and paint it.
 *
 *  Worth doing on launch and on every return to the foreground: a push may have been
 *  dropped, delivered to another device, or arrived while the phone was off, and this is
 *  the moment the true figure is cheap to get.
 */
export async function refreshBadge(): Promise<void> {
  if (!supported()) return;
  try {
    const { count } = await api.badge();
    await paint(count);
  } catch {
    // Offline, or the session has expired. The icon keeps its last value, which is a
    // better answer than zero.
  }
}

/** Mark the inbox looked at and clear the icon.
 *
 *  Paints what the server returns rather than assuming zero, because an episode can land
 *  between the request and the response.
 */
export async function clearBadge(): Promise<void> {
  try {
    const { count } = await api.markInboxSeen();
    await paint(count);
  } catch {
    // The badge stays until the next launch, which is the safe direction to fail in.
  }
}
