import type { MetaOut } from "../api";

function relative(from: Date, to = new Date()): string {
  const hours = Math.round((to.getTime() - from.getTime()) / 36e5);
  if (hours < 1) return "just now";
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Shows when the data was last scraped, in the viewer's local time. */
export function LastUpdated({ meta }: { meta: MetaOut }) {
  const at = new Date(meta.scraped_at);
  return (
    <p className={"last-updated" + (meta.stale ? " stale" : "")}>
      Last updated{" "}
      <time dateTime={at.toISOString()} title={at.toISOString()}>
        {at.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
      </time>{" "}
      ({relative(at)}) · {meta.row_count.toLocaleString()} runs · {meta.player_count.toLocaleString()} players ·{" "}
      {meta.game_count.toLocaleString()} games
      {meta.stale && " · data is older than expected"}
    </p>
  );
}
