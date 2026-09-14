import { notFound } from "next/navigation";
import { ServicingTerminal } from "./terminal";

export default async function ServicingPage({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  if (tenant !== "harbor" && tenant !== "summit") notFound();
  return <ServicingTerminal tenant={tenant} />;
}
