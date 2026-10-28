/**
 * WP-W1 / DD-19: "구조 상세" in TrainingPanel for a baseline registry id (GET saved/declared + explicit CPU check) and for a
 * user YAML path on the server (POST /structure-check). The server is a fetch mock; documents are real server output
 * (tests/structure_v2_server_fixture.json) wrapped with the API fields of CONTRACTS_v2 section 2.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { ApiClient } from '../src/api/client';
import { RegistryStructureButton, parseInputSize } from '../src/components/RegistryStructureButton';
import { TrainingPanel } from '../src/components/TrainingPanel';
import fixture from './structure_v2_server_fixture.json';

const docs = fixture.documents as unknown as Record<string, Record<string, unknown>>;
afterEach(cleanup);

interface Call { method: string; path: string; body: unknown }
function clientFor(answer: (call: Call) => { status: number; json: unknown }): { client: ApiClient; calls: Call[] } {
  const calls: Call[] = [];
  const fetchImpl = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(String(input), 'http://x');
    const call: Call = { method: init.method ?? 'GET', path: url.pathname, body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const { status, json } = answer(call);
    return new Response(JSON.stringify(json), { status, headers: { 'content-type': 'application/json' } });
  };
  return { client: new ApiClient({ origin: '', token: 'T', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch }), calls };
}

const declaredOnly = (id: string): Record<string, unknown> => ({
  ...docs.declared_only, model_id: id, status: 'UNVERIFIED', evidence_source: 'declared_configuration',
  checks: [{ name: 'cpu_build', status: 'NOT_CHECKED', evidence_level: null }],
  diagnostics: [{ code: 'STRUCTURE_CHECK_NOT_RECORDED', position: null, path: null, cause: 'no structure check was recorded for this model', suggestion: 'run the CPU structure check' }],
});
const checked = (id: string): Record<string, unknown> => ({ ...docs.base, model_id: id, evidence_source: 'executed_structure_check' });

describe('parseInputSize', () => {
  it('accepts empty and positive multiples of 32 only', () => {
    expect(parseInputSize('')).toEqual({ ok: true, value: undefined });
    expect(parseInputSize(' 640 ')).toEqual({ ok: true, value: 640 });
    for (const bad of ['31', '100', '0', '-32', '64.5', 'abc']) expect(parseInputSize(bad).ok, bad).toBe(false);
  });
});

describe('RegistryStructureButton (baseline registry id)', () => {
  it('is disabled without a model id or a connection', () => {
    const { client } = clientFor(() => ({ status: 200, json: {} }));
    const { rerender } = render(<RegistryStructureButton client={client} kind="baseline" model="" />);
    expect(screen.getByTestId('registry-structure-open')).toBeDisabled();
    rerender(<RegistryStructureButton client={null} kind="baseline" model="yolo11n" />);
    expect(screen.getByTestId('registry-structure-open')).toBeDisabled();
    rerender(<RegistryStructureButton client={client} kind="baseline" model="yolo11n" />);
    expect(screen.getByTestId('registry-structure-open')).toBeEnabled();
  });

  it('GET shows the declared-only document with its notice and without any observation', async () => {
    const { client, calls } = clientFor((call) => call.path === '/api/v1/registry/yolo11n/structure' ? { status: 200, json: declaredOnly('yolo11n') } : { status: 404, json: { code: 'NOT_FOUND', message: 'x' } });
    render(<RegistryStructureButton client={client} kind="baseline" model=" yolo11n " />);
    await userEvent.setup().click(screen.getByTestId('registry-structure-open'));
    const result = await screen.findByTestId('registry-structure-result');
    expect(calls).toEqual([{ method: 'GET', path: '/api/v1/registry/yolo11n/structure', body: undefined }]);
    expect(within(result).getByTestId('summary-evidence-source')).toHaveTextContent('declared_configuration');
    expect(within(result).getByTestId('structure-declared-only-notice')).toBeInTheDocument();
    expect(within(result).getByTestId('structure-summary')).toHaveTextContent('관측 기록 없음');
    expect(within(result).getByTestId('structure-status')).toHaveTextContent('UNVERIFIED');
    expect(within(result).getByText(/STRUCTURE_CHECK_NOT_RECORDED/)).toBeInTheDocument();
    expect(within(result).getAllByTestId(/^gene-/)).toHaveLength((docs.declared_only.blocks as unknown[]).length);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('runs the explicit CPU check (POST registry structure-check, input size sent only when valid) and renders the result with the same view', async () => {
    const { client, calls } = clientFor((call) => call.method === 'POST' ? { status: 200, json: checked('yolo11n') } : { status: 200, json: declaredOnly('yolo11n') });
    render(<RegistryStructureButton client={client} kind="baseline" model="yolo11n" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId('registry-structure-open'));
    await screen.findByTestId('registry-structure-result');
    // invalid size: nothing is sent
    await user.type(screen.getByTestId('registry-structure-size'), '100');
    await user.click(screen.getByTestId('registry-structure-check'));
    expect(await screen.findByText('입력 크기는 32 이상, 32의 배수여야 합니다.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
    await user.clear(screen.getByTestId('registry-structure-size'));
    await user.type(screen.getByTestId('registry-structure-size'), '64');
    await user.click(screen.getByTestId('registry-structure-check'));
    await waitFor(() => expect(screen.getByTestId('summary-evidence-source')).toHaveTextContent('executed_structure_check'));
    expect(calls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: '/api/v1/registry/yolo11n/structure-check', body: { input_size: 64 } }]);
    expect(screen.getByTestId('structure-status')).toHaveTextContent('PASS');
    expect(screen.queryByTestId('structure-declared-only-notice')).toBeNull();
    expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('최저 증거 수준: declared');
    expect(screen.getByTestId('registry-structure-result')).toHaveTextContent('방금 실행한 서버 CPU 검사');
  });

  it('shows the server error code when the model is unknown, and closes with Escape', async () => {
    const { client } = clientFor(() => ({ status: 404, json: { code: 'MODEL_NOT_FOUND', message: 'no such registry model' } }));
    render(<RegistryStructureButton client={client} kind="baseline" model="nope" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId('registry-structure-open'));
    expect(await screen.findByText(/MODEL_NOT_FOUND: no such registry model/)).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('RegistryStructureButton (user YAML on the server)', () => {
  it('does not GET (no stored evidence) and POSTs /structure-check with the path, size and class count', async () => {
    const { client, calls } = clientFor(() => ({ status: 200, json: { ...docs.base, evidence_source: undefined } }));
    render(<RegistryStructureButton client={client} kind="user" model="/data/models/mine.yaml" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId('registry-structure-open'));
    expect(await screen.findByTestId('registry-structure-none')).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await user.type(screen.getByTestId('registry-structure-size'), '64');
    await user.type(screen.getByTestId('registry-structure-nc'), '3');
    await user.click(screen.getByTestId('registry-structure-check'));
    await screen.findByTestId('registry-structure-result');
    expect(calls).toEqual([{ method: 'POST', path: '/api/v1/structure-check', body: { model_path: '/data/models/mine.yaml', input_size: 64, class_count: 3 } }]);
    expect(screen.getByTestId('structure-status')).toHaveTextContent('PASS');
    expect(screen.getByTestId('model-structure')).toBeInTheDocument();
  });

  it('shows a path rejection from the server (PATH_OUTSIDE_ALLOWLIST) and keeps no stale result', async () => {
    const { client } = clientFor(() => ({ status: 422, json: { code: 'PATH_OUTSIDE_ALLOWLIST', message: 'model_path is outside the allowed roots' } }));
    render(<RegistryStructureButton client={client} kind="user" model="/etc/passwd" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId('registry-structure-open'));
    await user.click(screen.getByTestId('registry-structure-check'));
    expect(await screen.findByText(/PATH_OUTSIDE_ALLOWLIST/)).toBeInTheDocument();
    expect(screen.queryByTestId('registry-structure-result')).toBeNull();
  });

  it('renders an UNVERIFIED answer with the dedicated "no observation" notice, not as a failure', async () => {
    const unsupported = { schema: 'mbnas-structure/2', status: 'UNVERIFIED', backend: 'rfdetr', representation: 'native_modules', blocks: [], edges: [], diagnostics: [{ code: 'STRUCTURE_RUNTIME_UNAVAILABLE', cause: 'torchvision::nms not compiled' }], checks: [] };
    const { client } = clientFor(() => ({ status: 200, json: unsupported }));
    render(<RegistryStructureButton client={client} kind="user" model="/data/models/rf.yaml" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId('registry-structure-open'));
    await user.click(screen.getByTestId('registry-structure-check'));
    expect(await screen.findByTestId('structure-unobserved-notice')).toHaveTextContent('구조 관측 없음 / 지원되지 않음');
    expect(screen.queryByTestId('structure-fail-notice')).toBeNull();
  });
});

describe('TrainingPanel integration', () => {
  it('offers 구조 상세 next to the model field and passes the typed id / path and source kind', async () => {
    const { client, calls } = clientFor((call) => call.path.endsWith('/structure') ? { status: 200, json: declaredOnly('yolo11n') } : { status: 200, json: checked('yolo11n') });
    render(<TrainingPanel client={client} protocol={{}} />);
    const user = userEvent.setup();
    const open = screen.getByTestId('registry-structure-open');
    expect(open).toBeDisabled();
    await user.type(screen.getByLabelText(/Baseline registry ID/), 'yolo11n');
    expect(open).toBeEnabled();
    await user.click(open);
    await screen.findByTestId('registry-structure-result');
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['GET /api/v1/registry/yolo11n/structure']);
    await user.keyboard('{Escape}');
    // switching to a user model: the server path is an advanced option now (1.5.0 default is the upload); with the
    // toggle on, the same button posts the YAML path
    await user.selectOptions(screen.getByLabelText('모델 출처'), 'user');
    expect(screen.queryByLabelText(/모델 구성 YAML 서버 절대경로/)).toBeNull();
    await user.click(screen.getByTestId('model-path-advanced'));
    await user.type(screen.getByLabelText(/모델 구성 YAML 서버 절대경로/), '/data/m.yaml');
    await user.click(screen.getByTestId('registry-structure-open'));
    await user.click(screen.getByTestId('registry-structure-check'));
    await screen.findByTestId('registry-structure-result');
    expect(calls[calls.length - 1]).toEqual({ method: 'POST', path: '/api/v1/structure-check', body: { model_path: '/data/m.yaml' } });
  });

  it('is not offered when the training source is fixed (candidate detail already shows its own structure)', () => {
    const { client } = clientFor(() => ({ status: 200, json: {} }));
    render(<TrainingPanel client={client} protocol={{}} source={{ kind: 'search', run_id: 'r', candidate_id: 'c' }} />);
    expect(screen.queryByTestId('registry-structure-open')).toBeNull();
  });
});
