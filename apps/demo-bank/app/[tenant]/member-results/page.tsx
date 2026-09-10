import Link from "next/link";
import { notFound } from "next/navigation";
import { UnexpectedDialog } from "./unexpected-dialog";

const members = {
  "12345": { name: "Alex Morgan", suffix: "0421" },
  "67890": { name: "Jordan Lee", suffix: "7782" },
} as const;

const delay = (milliseconds: number) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

export default async function MemberResults({
  params,
  searchParams,
}: {
  params: Promise<{ tenant: string }>;
  searchParams: Promise<{ member_id?: string; scenario?: string; continued?: string }>;
}) {
  const { tenant } = await params;
  const query = await searchParams;
  if (!new Set(["harbor", "summit"]).has(tenant)) notFound();
  const memberId = query.member_id ?? "";
  const scenario = query.scenario ?? "normal";
  if (scenario === "slow") await delay(1_500);

  if (scenario === "expired") {
    return <StatePage heading="Your session has expired" message="Return to Member Search to continue." />;
  }
  if (scenario === "permission") {
    return <StatePage heading="Permission denied" message="Your role cannot view this member record." />;
  }
  if (scenario === "interstitial" && query.continued !== "yes") {
    return (
      <main className="legacy-content">
        <h2>Important notice</h2>
        <p>Member records in this training environment contain synthetic data.</p>
        <Link className="button-link" href={`/${tenant}/member-results?member_id=${memberId}&scenario=interstitial&continued=yes`}>
          Continue
        </Link>
      </main>
    );
  }

  const member = members[memberId as keyof typeof members];
  if (!member) {
    return <StatePage heading="Member Results" message="No member found" />;
  }

  const rows = scenario === "duplicate" ? ["SAV-0421", "SAV-8890"] : ["SAV-0421"];
  return (
    <main className="legacy-content">
      {scenario === "dialog" ? <UnexpectedDialog /> : null}
      <div className="breadcrumb">Member Service &gt; Member Results</div>
      <h2>Member Results</h2>
      <dl className="member-summary">
        <div><dt>Member ID</dt><dd>{memberId}</dd></div>
        <div><dt>Member name</dt><dd>{member.name}</dd></div>
      </dl>
      <h3>Accounts</h3>
      <table className="account-table">
        <thead><tr><th>Account</th><th>Type</th><th>Status</th><th>Available</th><th>Action</th></tr></thead>
        <tbody>
          {rows.map((account, index) => (
            <tr key={account}>
              <td>•••• {index === 0 ? member.suffix : "8890"}</td><td>Savings</td><td>Open</td><td>$1,420.57</td>
              <td><Link href={`/${tenant}/accounts/${account}/details?member_id=${memberId}`}>View details</Link></td>
            </tr>
          ))}
          <tr><td>•••• 3901</td><td>Checking</td><td>Open</td><td>$892.14</td><td>View details</td></tr>
        </tbody>
      </table>
    </main>
  );
}

function StatePage({ heading, message }: { heading: string; message: string }) {
  return <main className="legacy-content"><h2>{heading}</h2><div role="alert" className="state-message">{message}</div></main>;
}
