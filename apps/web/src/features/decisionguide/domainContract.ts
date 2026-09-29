// 판정 안내 화면이 인용하는 정본 문구. 새 기준을 만들지 않고 아래 출처의 문구만 옮긴다.
// test/submission-extended-demo.mjs가 각 항목을 출처 파일과 대조한다.
export const SOURCES = {
  original: "sources/PROJECT_DOMAIN_V2_ORIGINAL.md",
  decisions: "docs/R00_DOMAIN_DECISIONS.md",
  gaps: "contracts/domain_gaps.json",
  manifest: "config/rule_pack_manifest.yaml",
  engine: "packages/proofops/domain/rules/engine.py",
  values: "packages/proofops/domain/values.py",
} as const;

/** engine.py ENGINE_VERSION */
export const ENGINE_VERSION = "explicit-ladders-exceptions-3";

/** 원문 §4.1 */
export const SPLIT = [
  { owner: "LLM 계층", does: "클레임 추출, 트랙 분류, 입증요소의 존재 여부 태깅, 근거 위치 지정", doesNot: "등급 부여, 라벨 결정" },
  { owner: "규칙엔진", does: "태깅 결과를 사전 고정된 임계값에 대입해 E등급과 라벨을 결정론적으로 산출", doesNot: "의미 해석" },
];

/** 원문 §4.3 (라벨 매핑은 values.py GRADE_LABEL_MAP과 동일) */
export const GRADES = [
  { grade: "E3", label: "SUBSTANTIATED", meaning: "입증요소를 충분히 갖춤" },
  { grade: "E2", label: "INCOMPLETE", meaning: "핵심 요소는 있으나 검증 연결이 미비" },
  { grade: "E1", label: "INCOMPLETE", meaning: "주장은 있으나 확인에 필요한 요소가 부족" },
  { grade: "E0", label: "UNSUBSTANTIATED", meaning: "입증요소가 사실상 부재" },
];

/** 원문 §4.4 */
export const LADDERS = [
  { track: "goal", title: "목표·미래지향형", basis: "IFRS S2 목표 공시 요구사항", rows: [
    ["E0", "목표연도 부재. 단 목표수치와 전환계획이 모두 있으면 E1을 상한으로 인정"],
    ["E1", "목표연도 + 목표수치(지표)"],
    ["E2", "E1 + 기준연도·기준값 + 적용범위(Scope·조직경계)"],
    ["E3", "E2 + 현재 이행률(진척) + 전환계획·달성수단"],
  ] },
  { track: "performance", title: "성과·실적형", basis: "IFRS S1 충실한 표현·검증가능성", rows: [
    ["E0", "정량수치 부재"],
    ["E1", "정량수치 + 단위"],
    ["E2", "E1 + 비교기준(기준연도·전년) + 산정범위"],
    ["E3", "E2 + 산정방법 명시 + 보증 연결 확인"],
  ] },
  { track: "management", title: "관리체계·이행형", basis: null, rows: [
    ["E0", "의지 표현만 (\"노력하고 있습니다\")"],
    ["E1", "명명된 표준·수단 또는 구체적 시행 상태"],
    ["E2", "E1 + 적용범위(조직경계·사업장)"],
    ["E3", "E2 + 외부검증"],
  ] },
] as const;

/** 원문 §4.4 최상급 주장 특칙 */
export const SUPERLATIVE = "\"세계 최초\", \"업계 최고\" 등 비교·최상급 주장은 비교기준(비교 대상군·측정단위·출처)과 외부검증(제3자 검증·인증기관명·공인출처)이 모두 부재하면 트랙과 무관하게 E0으로 분류합니다.";

/** 원문 §4.5 필수/조건부 구분 */
export const CONDITIONAL: Record<string, string> = {
  G7: "상쇄를 언급하거나 탄소중립을 주장할 때만",
  G8: "SBTi 등 과학기반을 주장할 때만",
  P5: "감축률·개선율을 주장할 때",
  M5: "거버넌스를 주장하는 클레임에 한함",
  M6: "보상 연계를 주장하는 클레임에 한함",
};

/** 등급 사다리 요소 (engine table LADDER / 원문 §4.4). 나머지는 R00 §7 A-2의 추가 요소 */
export const LADDER_ELEMENTS: Record<string, string[]> = {
  management: ["M1", "M2", "M3"],
  performance: ["P1", "P2", "P3", "P4"],
  goal: ["G1", "G2", "G3", "G4", "G5", "G6"],
};

/** R00 §7 A-2, §8 */
export const DECISIONS = {
  additional: "등급은 §4.4 사다리 요소만으로 계산한다. §4.5의 추가 요소 (G7·G8·P5·P6·M4·M5·M6)는 등급을 바꾸지 않고 missing_elements/unresolved_elements에 남긴다.",
  range: "등급·라벨이 아니며 evidence_grade/label은 계속 null이다. unknown을 absent로 바꾸지 않는다.",
};

/** values.py ELEMENT_STATES 와 engine.py ConfirmedFact 검증 규칙 */
export const ELEMENT_STATES = [
  { state: "present", rule: "검증된 인용(citation_verified)과 수락된 바인딩, 근거 위치가 모두 있어야 합니다." },
  { state: "absent", rule: "검색 범위 검증(search_coverage_verified)이 있어야만 인정됩니다." },
  { state: "unknown", rule: "아직 판단할 수 없음. 근거 부재로 바꾸지 않습니다." },
  { state: "conflict", rule: "근거끼리 맞지 않음. 근거 부재로 바꾸지 않습니다." },
  { state: "not_applicable", rule: "해당하지 않는 항목은 미충족이 아니라 결측으로 처리합니다 (원문 §4.8)." },
];

/** contracts/domain_gaps.json (issue 필드 원문) */
export const GAPS = [
  { id: "GAP-001", source_section: "§4.6", issue: "세이프하버 체크리스트는 있지만 E0~E3·reasonable_basis boolean 판정 조합이 완전하지 않음" },
  { id: "GAP-002", source_section: "§4.2/4.6/§7", issue: "목표형과 미래 예측 정보의 포함관계가 완전한 분류 규칙으로 정의되지 않음" },
  { id: "GAP-003", source_section: "§4.4/4.5", issue: "사다리와 추가 필수/조건부 항목(P6,M4,G7/G8 등)의 최종 등급 영향이 명시되지 않은 조합" },
  { id: "GAP-004", source_section: "§6 2-4 / §7", issue: "정량/연도 직접근거와 전역인정 사이에서 기준연도·진척·계획/explicit_link 범위 일부 모호" },
  { id: "GAP-005", source_section: "§4.3/§7", issue: "INCOMPLETE(PERF/IMPL)의 모든 트랙·복합결손 매핑이 없음" },
  { id: "GAP-006", source_section: "§4.4/4.6", issue: "모든 트랙 최상급 우선과 safe harbor 별도 경로가 겹칠 때 우선순위 미명시" },
  { id: "GAP-007", source_section: "§4.4", issue: "목표연도만 있고 목표수치 없음, 수치만 있고 unit 없음, 제품 변형의 일반 경계와의 결합 등 일부 경계 누락" },
  { id: "GAP-008", source_section: "§3.6/13.2", issue: "조항번호/기준대응표/재배포 권리 미확정" },
  { id: "GAP-009", source_section: "§1.2/4.9/13.1", issue: "법제·발효·유예·세이프하버 사실을 현재 시행법으로 재검증한 근거 미제공" },
  { id: "GAP-010", source_section: "§4.8", issue: "실제 GICS→SASB 산업 mapping 및 topic별 필수/선택 조합 미제공" },
];

/** config/rule_pack_manifest.yaml unresolved_gap_ids (impl2) */
export const UNRESOLVED_GAP_IDS = ["GAP-001", "GAP-002", "GAP-004", "GAP-005", "GAP-006", "GAP-007", "GAP-008", "GAP-009", "GAP-010"];
