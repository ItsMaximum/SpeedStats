import type { MetaOut } from "../api";

/** When the data was last scraped, in the viewer's local time. Shown inside the query form. */
export function LastUpdated({ meta }: { meta: MetaOut }) {
  const at = new Date(meta.scraped_at);
  return (
    <p className={"last-updated" + (meta.stale ? " stale" : "")}>
      Last updated{" "}
      <time dateTime={at.toISOString()} title={at.toISOString()}>
        {at.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
      </time>
      {meta.stale && " · data is older than expected"}
    </p>
  );
}

/** Dataset size, shown under the results. */
export function DataCounts({ meta }: { meta: MetaOut }) {
  return (
    <p className="data-counts">
      {meta.row_count.toLocaleString()} runs · {meta.player_count.toLocaleString()} players ·{" "}
      {meta.game_count.toLocaleString()} games
    </p>
  );
}
