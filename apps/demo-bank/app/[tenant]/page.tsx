import { notFound, redirect } from "next/navigation";

export default async function TenantPage({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  if (tenant !== "harbor" && tenant !== "summit") notFound();
  redirect(`/${tenant}/servicing`);
}
