import { notFound } from "next/navigation";
import { VisualWorkbench } from "./visual-workbench";

const validTenants = new Set(["harbor", "summit"]);

export default async function VisualWorkbenchPage({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  if (!validTenants.has(tenant)) notFound();
  return <VisualWorkbench tenant={tenant as "harbor" | "summit"} />;
}
