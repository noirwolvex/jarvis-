import { ControlCenter } from "@/components/control-center";
import { HybridControlCenter } from "@/components/hybrid-control-center";
import { NativeControlCenter } from "@/components/native-control-center";

export const dynamic = "force-dynamic";

export default function Home() {
  const mode = process.env.JARVIS_CONTROL_MODE?.trim().toLowerCase();
  if (mode === "native") return <NativeControlCenter />;
  if (mode === "hybrid") return <HybridControlCenter />;
  return <ControlCenter />;
}
