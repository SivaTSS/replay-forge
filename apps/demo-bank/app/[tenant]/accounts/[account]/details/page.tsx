import Link from "next/link";
import { notFound } from "next/navigation";

export default async function AccountDetails({
  params,
  searchParams,
}: {
  params: Promise<{ tenant: string; account: string }>;
  searchParams: Promise<{ member_id?: string }>;
}) {
  const { tenant, account } = await params;
  const { member_id: memberId } = await searchParams;
  if (!memberId || !account.startsWith("SAV-")) notFound();

  return (
    <main className="legacy-content">
      <div className="breadcrumb">Member Service &gt; Accounts &gt; Details</div>
      <h2>Savings Account Details</h2>
      <dl className="detail-grid">
        <div><dt>Member ID</dt><dd>{memberId}</dd></div>
        <div><dt>Account type</dt><dd>Savings</dd></div>
        <div><dt>Currency</dt><dd>USD</dd></div>
        <div><dt>Available balance</dt><dd className="balance">$1,420.57</dd></div>
        <div><dt>As of</dt><dd>2026-09-10T12:30:00Z</dd></div>
        <div><dt>Status</dt><dd><span className="status-open">Open</span></dd></div>
      </dl>
      <Link href={`/${tenant}/member-search`}>Return to Member Search</Link>
    </main>
  );
}
