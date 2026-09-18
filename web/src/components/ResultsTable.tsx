import { useMemo, useState } from "react";
import type { Cell, PlayerStyle, QueryOut } from "../api";
import { linkTo, type BoxName, type RequestType } from "../query";

interface Props {
  result: QueryOut;
}

type Sort = { column: number; dir: 1 | -1 } | null;

/** Which query a click on a cell leads to. Leaderboard names are not linkable (they embed the game name). */
const CELL_LINKS: Record<string, [BoxName, RequestType]> = {
  Player: ["players", "runs"],
  Game: ["games", "pr"],
  Series: ["series", "pr"],
};

function compare(a: Cell, b: Cell): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

function formatCell(column: string, value: Cell): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" && (column === "Points" || column === "Value")) {
    return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 3 });
  }
  return String(value);
}

function Flag({ id, name }: { id: string; name: string }) {
  return <img className="flag" src={`/api/flags/${id}.png`} alt={name} title={name} loading="lazy" height={12} />;
}

/** A player name as speedrun.com shows it: flag, Inter bold, their colour or a two-colour gradient. */
function PlayerName({ name, style, href }: { name: string; style?: PlayerStyle; href: string }) {
  const gradient = style?.color1 && style.color2;
  const css = gradient
    ? ({ "--c1": style.color1, "--c2": style.color2 } as React.CSSProperties)
    : style?.color1
      ? { color: style.color1 }
      : undefined;
  return (
    <>
      {style?.flag && <Flag id={style.flag} name={style.flag_name ?? style.flag} />}
      <a href={href} className={"username" + (gradient ? " username-gradient" : "")} style={css}>
        {name}
      </a>
    </>
  );
}

export function ResultsTable({ result }: Props) {
  const [sort, setSort] = useState<Sort>(null);

  const rows = useMemo(() => {
    if (!sort) return result.rows;
    const { column, dir } = sort;
    return [...result.rows].sort((a, b) => dir * compare(a[column], b[column]));
  }, [result.rows, sort]);

  function toggle(column: number) {
    setSort((s) => {
      if (!s || s.column !== column) return { column, dir: column === 0 ? 1 : -1 };
      if ((column === 0 && s.dir === 1) || (column !== 0 && s.dir === -1)) return { column, dir: (-s.dir) as 1 | -1 };
      return null;
    });
  }

  if (result.rows.length === 0) {
    return <p className="empty">No results.</p>;
  }

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            {result.columns.map((col, i) => (
              <th key={col} onClick={() => toggle(i)} aria-sort={sort?.column === i ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                {col}
                {sort?.column === i && <span className="sort-indicator">{sort.dir === 1 ? " ▲" : " ▼"}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => {
                const col = result.columns[c];
                const link = CELL_LINKS[col];
                const text = formatCell(col, cell);
                if (col === "Player" && cell !== null && cell !== undefined) {
                  return (
                    <td key={c} className="player">
                      <PlayerName name={String(cell)} style={result.players[String(cell)]} href={linkTo("players", String(cell), "runs")} />
                    </td>
                  );
                }
                return (
                  <td key={c} className={typeof cell === "number" ? "num" : undefined}>
                    {link && cell !== null && cell !== undefined ? <a href={linkTo(link[0], String(cell), link[1])}>{text}</a> : text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
