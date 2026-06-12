import assert from 'node:assert/strict';
import test from 'node:test';
import type { ParsedCliTail } from './options.js';
import { normalizeResumeSyncCliOptions } from './options.js';

test('normalizeResumeSyncCliOptions parses recommend defaults', () => {
  const options = normalizeResumeSyncCliOptions({
    rest: [],
    flags: new Set<string>(),
    opts: { from: 'recommend' },
  });

  assert.deepEqual(options, {
    source: 'recommend',
    limit: 20,
    unreadOnly: false,
    jsonOutput: false,
    keyword: undefined,
    city: undefined,
    jobKeyword: undefined,
    rootDir: undefined,
  });
});

test('normalizeResumeSyncCliOptions parses json flag', () => {
  const options = normalizeResumeSyncCliOptions({
    rest: [],
    flags: new Set<string>(['json']),
    opts: { from: 'chat', limit: '3' },
  });

  assert.equal(options.jsonOutput, true);
  assert.equal(options.limit, 3);
});

test('normalizeResumeSyncCliOptions parses search keyword with optional job and city', () => {
  const options = normalizeResumeSyncCliOptions({
    rest: [],
    flags: new Set<string>(['json']),
    opts: { from: 'search', keyword: '后端', job: 'Java', city: '广州', limit: '3' },
  });

  assert.equal(options.source, 'search');
  assert.equal(options.keyword, '后端');
  assert.equal(options.jobKeyword, 'Java');
  assert.equal(options.city, '广州');
  assert.equal(options.limit, 3);
  assert.equal(options.jsonOutput, true);
});

test('normalizeResumeSyncCliOptions rejects invalid source', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'invalid' },
      }),
    /--from/,
  );
});

test('normalizeResumeSyncCliOptions rejects deep-search source', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'deep-search' },
      }),
    /--from search/,
  );
});

test('normalizeResumeSyncCliOptions rejects --job with chat', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'chat', job: 'python' },
      }),
    /--keyword \/ --job \/ --city/,
  );
});

test('normalizeResumeSyncCliOptions rejects --keyword with chat', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'chat', keyword: 'Java' },
      }),
    /--keyword \/ --job \/ --city/,
  );
});

test('normalizeResumeSyncCliOptions rejects --city with chat', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'chat', city: '广州' },
      }),
    /--keyword \/ --job \/ --city/,
  );
});

test('normalizeResumeSyncCliOptions rejects --unread outside chat', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(['unread']),
        opts: { from: 'recommend' },
      }),
    /--unread/,
  );
});

test('normalizeResumeSyncCliOptions rejects non-positive limit', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'chat', limit: '0' },
      }),
    /--limit/,
  );
});

test('normalizeResumeSyncCliOptions rejects search without keyword', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'search' },
      }),
    /--keyword/,
  );
});

test('normalizeResumeSyncCliOptions rejects keyword and city with recommend', () => {
  assert.throws(
    () =>
      normalizeResumeSyncCliOptions({
        rest: [],
        flags: new Set<string>(),
        opts: { from: 'recommend', keyword: '后端', city: '广州' },
      }),
    /recommend/,
  );
});

test('normalizeResumeSyncCliOptions rejects old search/core/bonus flags', () => {
  const cases: ParsedCliTail[] = [
    { rest: [], flags: new Set<string>(['search']), opts: { from: 'search', keyword: 'Java' } },
    { rest: [], flags: new Set<string>(), opts: { from: 'search', keyword: 'Java', core: 'Java' } },
    { rest: [], flags: new Set<string>(), opts: { from: 'search', keyword: 'Java', bonus: '实习' } },
  ];
  for (const parsed of cases) {
    assert.throws(() => normalizeResumeSyncCliOptions(parsed), /--search \/ --core \/ --bonus/);
  }
});
