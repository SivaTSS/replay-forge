import { notFound } from "next/navigation";
import { VisualTerminal } from "./visual-terminal";

const validTenants = new Set(["harbor", "summit"]);

export default async function VisualTerminalPage({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  if (!validTenants.has(tenant)) notFound();
  return <VisualTerminal tenant={tenant as "harbor" | "summit"} />;
}
