import { removeExtensionHydrationMarkers } from "./lib/hydration-compatibility";

// Next runs synchronous client instrumentation after loading the HTML and before
// React hydration. An effect would run too late to repair these external markers.
try {
  removeExtensionHydrationMarkers(document);
} catch (error) {
  console.warn("JARVIS could not clean extension-added hydration markers", error);
}
