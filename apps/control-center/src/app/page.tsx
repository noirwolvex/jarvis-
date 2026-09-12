import { ControlCenter } from "@/components/control-center";
import { NativeControlCenter } from "@/components/native-control-center";

export default function Home() {
  const mode = process.env.JARVIS_CONTROL_MODE?.trim().toLowerCase();
  return mode === "native" ? <NativeControlCenter /> : <ControlCenter />;
}
