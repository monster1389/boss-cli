import type { ResumeSource } from './types.js';

export type ResumeSyncCliOptions = {
  source: ResumeSource;
  limit: number;
  unreadOnly: boolean;
  jsonOutput: boolean;
  keyword?: string;
  city?: string;
  jobKeyword?: string;
  rootDir?: string;
};

export type ParsedCliTail = {
  rest: string[];
  flags: Set<string>;
  opts: Record<string, string>;
};

function readPositiveInt(raw: string | undefined, fallbackValue: number): number {
  if (!raw) {
    return fallbackValue;
  }
  const value = Number.parseInt(raw.trim(), 10);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error('❌ resumes 的 --limit 必须是正整数。');
  }
  return value;
}

function parseResumeSource(raw: string | undefined): ResumeSource {
  const value = (raw ?? '').trim().toLowerCase();
  if (value === 'chat' || value === 'recommend' || value === 'search') {
    return value;
  }
  if (value === 'deep-search') {
    throw new Error('❌ resumes --from deep-search 已移除，请改用 --from search --keyword <关键词>。boss deep-search 命令保持不变。');
  }
  throw new Error('❌ 用法: resumes --from <chat|recommend|search> [--keyword <关键词>] [--job <岗位>] [--city <城市>] [--limit <数量>] [--unread] [--root <目录>] [--json]');
}

export function normalizeResumeSyncCliOptions(parsed: ParsedCliTail): ResumeSyncCliOptions {
  if (parsed.flags.has('search') || parsed.opts.core !== undefined || parsed.opts.bonus !== undefined) {
    throw new Error('❌ resumes 已不支持 --search / --core / --bonus；搜索简历请使用 --from search --keyword <关键词>。');
  }

  const unknownShortFlags = Array.from(parsed.flags).filter((flag) => flag !== 'unread' && flag !== 'json');
  if (unknownShortFlags.length > 0) {
    throw new Error(`❌ resumes 不支持 flag: -${unknownShortFlags.join(', -')}`);
  }

  const source = parseResumeSource(parsed.opts.from);
  const limit = readPositiveInt(parsed.opts.limit, 20);
  const unreadOnly = parsed.flags.has('unread');
  const jsonOutput = parsed.flags.has('json');
  const jobKeyword = parsed.opts.job?.trim();
  const keyword = parsed.opts.keyword?.trim();
  const city = parsed.opts.city?.trim();
  const rootDir = parsed.opts.root?.trim();
  const positional = parsed.rest.map((item) => item.trim()).filter(Boolean);

  if (positional.length > 0) {
    throw new Error('❌ resumes 不接受位置参数，请使用 --from / --keyword / --job / --city / --limit / --unread / --root / --json。');
  }
  if (source !== 'chat' && unreadOnly) {
    throw new Error('❌ 只有 resumes --from chat 支持 --unread。');
  }
  if (source === 'chat' && (jobKeyword || keyword || city)) {
    throw new Error('❌ resumes --from chat 不支持 --keyword / --job / --city。');
  }
  if (source === 'recommend' && (keyword || city)) {
    throw new Error('❌ resumes --from recommend 只支持可选 --job，不支持 --keyword / --city。');
  }
  if (source === 'search' && !keyword) {
    throw new Error('❌ resumes --from search 必须指定 --keyword <关键词>。');
  }

  const supportedKeys = new Set(['from', 'job', 'keyword', 'city', 'limit', 'root']);
  const extraKeys = Object.keys(parsed.opts).filter((key) => !supportedKeys.has(key));
  if (extraKeys.length > 0) {
    throw new Error(`❌ resumes 不支持参数: --${extraKeys.join(', --')}`);
  }

  return {
    source,
    limit,
    unreadOnly,
    jsonOutput,
    keyword: keyword || undefined,
    city: city || undefined,
    jobKeyword: jobKeyword || undefined,
    rootDir: rootDir || undefined,
  };
}
