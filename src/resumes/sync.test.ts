import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRecruiterResumeInfoUrl, matchSearchJobCandidate } from './sync.js';

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
