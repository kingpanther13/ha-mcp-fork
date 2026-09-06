// Evaluate the actual token-bearing job guard with GitHub's expression library.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const engineRequire = createRequire(join(process.env.RENOVATE_TEST_ROOT, 'package.json'));
const { parse } = engineRequire('yaml');
const { Lexer, Parser, Evaluator, data } = await import(pathToFileURL(
  '/tmp/expression-tests/node_modules/@actions/expressions/dist/index.js'
).href);
const { truthy } = await import(pathToFileURL(
  '/tmp/expression-tests/node_modules/@actions/expressions/dist/result.js'
).href);
const workflow = parse(readFileSync('/source/.github/workflows/renovate.yml', 'utf8'));
const expression = new Parser(new Lexer(workflow.jobs.renovate.if).lex().tokens, ['github'], []).parse();
const dashboard = {
  event_name: 'issues',
  event: {
    issue: { title: 'Dependency Dashboard', user: { login: 'ha-mcp-renovate[bot]' }, body: '- [x] request' },
    sender: { type: 'User' }, changes: { body: { from: '- [ ] request' } },
  },
};
const cases = [
  ['human checked request', () => {}, true],
  ['uppercase checked request', (g) => { g.event.issue.body = '- [X] request'; }, true],
  ['bot edit', (g) => { g.event.sender.type = 'Bot'; }, false],
  ['unrelated issue', (g) => { g.event.issue.title = 'Other issue'; }, false],
  ['spoofed dashboard author', (g) => { g.event.issue.user.login = 'someone-else'; }, false],
  ['unchanged body', (g) => { delete g.event.changes.body; }, false],
  ['unchecked body', (g) => { g.event.issue.body = '- [ ] request'; }, false],
  ['manual dispatch', (g) => { g.event_name = 'workflow_dispatch'; g.event = {}; }, true],
  ['hourly schedule', (g) => { g.event_name = 'schedule'; g.event = {}; }, true],
];
for (const [name, mutate, expected] of cases) {
  const github = structuredClone(dashboard);
  mutate(github);
  const context = JSON.parse(JSON.stringify({ github }), data.reviver);
  // Logical expressions can short-circuit to null; a job guard uses truthiness,
  // not the string representation of the result.
  assert.equal(truthy(new Evaluator(expression, context).evaluate()), expected, name);
}
console.log(`GitHub expression evaluator passed ${cases.length} dashboard-event cases.`);
