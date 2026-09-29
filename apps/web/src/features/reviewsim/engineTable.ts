// scripts/build_engine_table.py가 고정 규칙집으로 순수 Python 규칙엔진(evaluate)을 미리 실행해 만든 조회표.
// 브라우저는 등급을 계산하지 않고, 사다리 요소 상태 조합에 해당하는 저장된 엔진 출력을 찾기만 한다.
export const TABLE_PATH = "demo/engine-table.json";
export const SIM_STATES = ["present", "unknown", "absent", "conflict"] as const;
export type SimState = typeof SIM_STATES[number];
export type Track = "management" | "performance" | "goal";
/** [status, evidence_grade, label, [floor, ceiling, open]|null, rule_ids, gap_ids, missing, unresolved] */
export type EngineRow = [string, string | null, string | null, [string, string, string[]] | null, string[], string[], string[], string[]];
export type EngineTable = {
  rule_pack_sha256: string;
  state_codes: Record<string, string>;
  tracks: Record<Track, { ladder: string[]; other: string[]; rows: Record<string, EngineRow> }>;
};

const code: Record<SimState, string> = { present: "p", unknown: "u", absent: "a", conflict: "n" };
export const isTrack = (value: string | null): value is Track => value === "management" || value === "performance" || value === "goal";
export const toSimState = (state: string): SimState | null => (SIM_STATES as readonly string[]).includes(state) ? state as SimState : null;

export function scenarioKey(table: EngineTable, track: Track, states: Record<string, SimState>, willingnessOnly: boolean): string {
  const group = table.tracks[track];
  const vector = group.ladder.map(id => code[states[id] ?? "unknown"]).join("");
  // 추가 요소는 등급에 영향이 없고(R00 §7 A-2) 조회표는 미확정 여부만 구분한다.
  const other = group.other.some(id => (states[id] ?? "unknown") === "unknown" || states[id] === "conflict") ? "1" : "0";
  return `${vector}${other}${track === "management" ? Number(willingnessOnly) : ""}`;
}

export function lookup(table: EngineTable, track: Track, states: Record<string, SimState>, willingnessOnly: boolean): EngineRow | undefined {
  return table.tracks[track].rows[scenarioKey(table, track, states, willingnessOnly)];
}

export function rowGradeText(row: EngineRow | undefined): string {
  if (!row) return "조회 불가";
  if (row[1]) return row[1];
  return row[3] ? `${row[3][0]}–${row[3][1]} 가능 범위` : "등급 없음 (null)";
}

let tablePromise: Promise<EngineTable> | undefined;
export function loadEngineTable(): Promise<EngineTable> {
  tablePromise ??= fetch(`${import.meta.env.BASE_URL}${TABLE_PATH}`)
    .then(response => { if (!response.ok) throw new Error("조회표를 불러오지 못했습니다."); return response.json() as Promise<EngineTable>; })
    .catch(error => { tablePromise = undefined; throw error; });
  return tablePromise;
}
