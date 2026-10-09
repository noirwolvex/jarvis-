// Some browser extensions annotate server-rendered elements before React starts.
// Remove only the observed extension markers, once, rather than suppressing real
// hydration diagnostics or changing the application's server-rendered content.
const processedMarker = /^__processed_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}__$/i;

export function removeExtensionHydrationMarkers(document: Document): void {
  const markedElements = document.querySelectorAll('[bis_skin_checked="1"]');
  const body = document.body;
  const hasRegistration = body?.hasAttribute("bis_register") ?? false;

  if (markedElements.length === 0 && !hasRegistration) return;

  for (const element of markedElements) {
    element.removeAttribute("bis_skin_checked");
  }
  if (!body) return;

  body.removeAttribute("bis_register");
  // An arbitrary __processed_* attribute alone is not evidence of this extension.
  // Only remove its UUID marker when accompanied by the known bis_* markers.
  for (const name of body.getAttributeNames()) {
    if (processedMarker.test(name) && body.getAttribute(name) === "true") {
      body.removeAttribute(name);
    }
  }
}
