// Structural OpenAPI 3.1 validation with @redocly/openapi-core (dependency of openapi-typescript in mbnas_web).
import { createRequire } from 'node:module';
const require = createRequire(process.argv[2] + '/');
const { lint, createConfig } = require('@redocly/openapi-core');
const files = process.argv.slice(3);
const config = await createConfig({ rules: { struct: 'error', 'no-unresolved-refs': 'error', 'path-parameters-defined': 'error', 'operation-operationId-unique': 'error', 'no-identical-paths': 'error', 'path-declaration-must-exist': 'error', 'operation-parameters-unique': 'error', 'spec-components-invalid-map-name': 'error', 'no-enum-type-mismatch': 'error', 'no-path-trailing-slash': 'error', 'no-ambiguous-paths': 'warn', 'operation-2xx-response': 'warn', 'no-example-value-and-externalValue': 'error' } });
let bad = 0;
for (const f of files) {
  const problems = await lint({ ref: f, config });
  const errors = problems.filter((p) => p.severity === 'error');
  const warns = problems.filter((p) => p.severity === 'warn');
  console.log(JSON.stringify({ file: f, errors: errors.length, warnings: warns.length,
    first: problems.slice(0, 12).map((p) => ({ rule: p.ruleId, sev: p.severity, msg: p.message.slice(0, 160), at: (p.location?.[0]?.pointer || '').slice(0, 160) })) }));
  if (errors.length) bad = 1;
}
process.exit(bad);
