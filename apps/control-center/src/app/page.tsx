import { ControlCenter } from "@/components/control-center";
import { HybridControlCenter } from "@/components/hybrid-control-center";
import { NativeControlCenter } from "@/components/native-control-center";
import { ControlSessionGate } from "@/components/control-session-gate";

export const dynamic = "force-dynamic";

export default function Home() {
  const mode = process.env.JARVIS_CONTROL_MODE?.trim().toLowerCase();
  if (mode === "native") return <ControlSessionGate><NativeControlCenter /></ControlSessionGate>;
  if (mode === "hybrid") return <ControlSessionGate><HybridControlCenter /></ControlSessionGate>;
  return <ControlCenter />;
}
