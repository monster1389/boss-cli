import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildRecruiterResumeInfoUrl,
  hasSearchCityOverlayResidue,
  isSearchCityTextSelected,
  isSearchJobCandidatePollutedByCity,
  matchSearchJobCandidate,
  pickSearchCityCandidate,
  pickUniqueSearchCandidates,
  resolveSelectedSearchJob,
} from './sync.js';

test('buildRecruiterResumeInfoUrl targets recruiter geek info endpoint with identifiers', () => {
  const url = new URL(buildRecruiterResumeInfoUrl({
    encryptGeekId: 'geek 001',
    encryptJobId: 'job/001',
    securityId: 'sec+001',
  }));

  assert.equal(url.origin, 'https://www.zhipin.com');
  assert.equal(url.pathname, '/wapi/zpjob/view/geek/info');
  assert.equal(url.searchParams.get('encryptGeekId'), 'geek 001');
  assert.equal(url.searchParams.get('encryptJobId'), 'job/001');
  assert.equal(url.searchParams.get('securityId'), 'sec+001');
});

test('matchSearchJobCandidate matches search job options case-insensitively', () => {
  const match = matchSearchJobCandidate('java', 'Java');

  assert.equal(match?.matchMethod, 'exact');
  assert.equal(match?.normalized, 'java');
});

test('matchSearchJobCandidate matches longer BOSS job labels', () => {
  const match = matchSearchJobCandidate('java', 'Java _ 广州 80-130元/天');

  assert.equal(match?.matchMethod, 'contains');
});

test('matchSearchJobCandidate matches multi-term job keywords', () => {
  const match = matchSearchJobCandidate('Java 后端', '高级Java后端开发');

  assert.equal(match?.matchMethod, 'all_terms');
});

test('matchSearchJobCandidate returns null for unrelated jobs', () => {
  assert.equal(matchSearchJobCandidate('Java 后端', '产品经理'), null);
});

test('resolveSelectedSearchJob treats matching current job label as selected', () => {
  const match = resolveSelectedSearchJob('Java', 'Java');

  assert.equal(match?.raw, 'Java');
  assert.equal(match?.matchMethod, 'exact');
});

test('resolveSelectedSearchJob handles current job label when candidate list is empty', () => {
  const emptyCandidates: string[] = [];
  const match = resolveSelectedSearchJob('Java', 'Java');

  assert.equal(emptyCandidates.length, 0);
  assert.equal(match?.matchMethod, 'exact');
});

test('isSearchJobCandidatePollutedByCity detects city dropdown samples', () => {
  assert.equal(isSearchJobCandidatePollutedByCity(['热门', '北京', '上海', '天津', '重庆', '黑龙江']), true);
});

test('isSearchJobCandidatePollutedByCity does not flag job samples', () => {
  assert.equal(isSearchJobCandidatePollutedByCity(['Java', 'Java开发', 'Java后端', '后端开发']), false);
});

test('hasSearchCityOverlayResidue detects city overlay text when job candidates are empty', () => {
  const cityText = '城市 热门 北京 上海 天津 重庆 黑龙江 吉林 辽宁';

  assert.equal(hasSearchCityOverlayResidue(cityText, []), true);
});

test('pickUniqueSearchCandidates skips duplicate visibleGeekId and keeps scanning', () => {
  const picked = pickUniqueSearchCandidates([
    { source: 'search', name: 'A1', jobLabel: 'Java', visibleGeekId: 'a', sourceMeta: { listIndex: 0 } },
    { source: 'search', name: 'A2', jobLabel: 'Java', visibleGeekId: 'a', sourceMeta: { listIndex: 1 } },
    { source: 'search', name: 'B', jobLabel: 'Java', visibleGeekId: 'b', sourceMeta: { listIndex: 2 } },
    { source: 'search', name: 'C', jobLabel: 'Java', visibleGeekId: 'c', sourceMeta: { listIndex: 3 } },
  ], 3, 'Java');

  assert.deepEqual(picked.candidates.map((item) => item.visibleGeekId), ['a', 'b', 'c']);
  assert.equal(picked.rawCandidateCount, 4);
  assert.equal(picked.uniqueCandidateCount, 3);
  assert.equal(picked.duplicateCandidateCount, 1);
});

test('pickUniqueSearchCandidates keeps fallback candidates with different listIndex', () => {
  const picked = pickUniqueSearchCandidates([
    { source: 'search', name: 'A', jobLabel: 'Java', sourceMeta: { listIndex: 0 } },
    { source: 'search', name: 'A', jobLabel: 'Java', sourceMeta: { listIndex: 1 } },
    { source: 'search', name: 'B', jobLabel: 'Java', sourceMeta: { listIndex: 2 } },
  ], 3, 'Java');

  assert.deepEqual(picked.candidates.map((item) => item.name), ['A', 'A', 'B']);
  assert.equal(picked.duplicateCandidateCount, 0);
});

test('pickUniqueSearchCandidates preserves original listIndex after dedupe', () => {
  const picked = pickUniqueSearchCandidates([
    { source: 'search', name: 'A1', jobLabel: 'Java', visibleGeekId: 'a', sourceMeta: { listIndex: 0 } },
    { source: 'search', name: 'A2', jobLabel: 'Java', visibleGeekId: 'a', sourceMeta: { listIndex: 1 } },
    { source: 'search', name: 'B', jobLabel: 'Java', visibleGeekId: 'b', sourceMeta: { listIndex: 4 } },
  ], 2, 'Java');

  assert.deepEqual(picked.candidates.map((item) => item.sourceMeta.listIndex), [0, 4]);
});

test('isSearchCityTextSelected matches selected city text from city wrap', () => {
  assert.equal(isSearchCityTextSelected('广州', '广州'), true);
  assert.equal(isSearchCityTextSelected('当前城市 广州', '广州'), true);
});

test('pickSearchCityCandidate prefers exact city candidate', () => {
  assert.equal(pickSearchCityCandidate('广州', ['广州市', '广州']), '广州');
});

test('pickSearchCityCandidate allows contains match for city candidates', () => {
  assert.equal(pickSearchCityCandidate('广州', ['广州市']), '广州市');
});

test('pickSearchCityCandidate returns null when city is absent', () => {
  assert.equal(pickSearchCityCandidate('广州', ['深圳', '佛山']), null);
});
