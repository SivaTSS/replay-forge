import { notFound } from "next/navigation";

const validTenants = new Set(["harbor", "summit"]);

export default async function MemberSearch({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  if (!validTenants.has(tenant)) notFound();

  return (
    <main className="legacy-content">
      <div className="breadcrumb">Member Service &gt; Search</div>
      <h2>Member Search</h2>
      <p className="instructions">Enter a synthetic member ID to locate the member record.</p>
      <form method="get" action={`/${tenant}/member-results`} className="legacy-form">
        <table>
          <tbody>
            <tr>
              <th scope="row"><label htmlFor="memberNumber">Member ID</label></th>
              <td><input id="memberNumber" name="member_id" inputMode="numeric" required /></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="scenario">Runtime scenario</label></th>
              <td>
                <select id="scenario" name="scenario" defaultValue="normal">
                  <option value="normal">Normal</option>
                  <option value="slow">Slow result load</option>
                  <option value="interstitial">Known interstitial</option>
                  <option value="expired">Session expired</option>
                  <option value="permission">Permission denied</option>
                  <option value="duplicate">Duplicate savings rows</option>
                  <option value="dialog">Unexpected confirmation</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
        <div className="form-actions"><button type="submit">Search</button></div>
      </form>
      <aside className="test-note">
        Valid IDs: <code>12345</code> and <code>67890</code>. Unknown IDs exercise the business outcome.
      </aside>
    </main>
  );
}
