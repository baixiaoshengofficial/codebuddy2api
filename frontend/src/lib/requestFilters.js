const DIMENSIONS = ["period", "model", "client", "credential", "status"];
const SELECTIONS = ["model", "client", "credential", "status", "search"];

export const beijingInputTime = (time) =>
  new Date(time + 480 * 60000).toISOString().slice(0, 16);

export function createRequestFilters(now = Date.now()) {
  return {
    period: "today",
    model: "",
    client: "",
    credential: "",
    status: "",
    search: "",
    start: beijingInputTime(now - 86400000),
    end: beijingInputTime(now),
  };
}

export function parseRequestRange(start, end) {
  const range = [start, end].map((value) => Date.parse(`${value}+08:00`));
  if (!range.every(Number.isFinite) || range[0] >= range[1])
    throw new Error("请选择有效的开始和结束时间");
  if (range[1] - range[0] > 366 * 86400000)
    throw new Error("自定义范围不能超过 366 天");
  return range;
}

export function createAppliedRequestFilters(now = Date.now()) {
  const defaults = createRequestFilters(now);
  const [start, end] = parseRequestRange(defaults.start, defaults.end);
  return { ...defaults, start, end };
}

export function commitRequestFilters(applied, draft, action) {
  if (action === "search") return { ...applied, search: draft.search.trim() };
  if (action === "range") {
    const [start, end] = parseRequestRange(draft.start, draft.end);
    return { ...applied, period: "custom", start, end };
  }
  if (action === "dimensions")
    return {
      ...applied,
      ...Object.fromEntries(DIMENSIONS.map((key) => [key, draft[key]])),
    };
  throw new Error("未知的筛选操作");
}

export function requestQueryParams(applied, page, pageSize) {
  const params = new URLSearchParams({
    page,
    page_size: pageSize,
    offset: 480,
  });
  for (const key of ["period", ...SELECTIONS])
    if (applied[key]) params.set(key, applied[key]);
  if (applied.period === "custom") {
    params.set("start", applied.start);
    params.set("end", applied.end);
  }
  return params;
}

export function activeRequestFilterCount(applied) {
  return (
    SELECTIONS.filter((key) => Boolean(applied[key])).length +
    (applied.period === "today" ? 0 : 1)
  );
}
