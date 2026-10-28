/**
 * WP-6B / F5a (contract 1.5.0): the model configuration upload flow of the Settings TrainingPanel. A file picked in the
 * browser goes unchanged as a raw body to POST /uploads/model-config; the answer's upload_id (+ the file name as
 * model_name) is what POST /structure-check and POST /training-runs receive; the legacy server model_path exists only
 * behind the "고급: 서버 경로" toggle and is exclusive with the upload. Synthetic bytes, fetch-level mock, no server.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { ApiClient, MODEL_CONFIG_MAX_BYTES } from '../src/api/client';
import { precheckModelFile, uploadErrorText } from '../src/components/ModelConfigUpload';
import { TrainingPanel } from '../src/components/TrainingPanel';
import { ApiError } from '../src/api/client';
import { fixtureUploadResponse, ORIGINAL_YAML, sha256Hex } from './config_fixtures';
import { fixtureDesignExampleProtocol } from './fixtures';
import fixture from './structure_v2_server_fixture.json';

afterEach(cleanup);

interface Seen { method: string; path: string; headers: Record<string, string>; json: unknown; raw: Blob | null }

function recorder(answer: (call: Seen) => { status: number; json: unknown } | Promise<{ status: number; json: unknown }>): { client: ApiClient; seen: Seen[] } {
  const seen: Seen[] = [];
  const fetchImpl = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(String(input), 'http://x');
    const raw = init.body instanceof Blob ? init.body : null;
    const call: Seen = { method: init.method ?? 'GET', path: url.pathname + url.search, headers: { ...(init.headers as Record<string, string>) }, json: typeof init.body === 'string' ? JSON.parse(init.body) : undefined, raw };
    seen.push(call);
    const { status, json } = await answer(call);
    return new Response(JSON.stringify(json), { status, headers: { 'content-type': 'application/json' } });
  };
  return { client: new ApiClient({ origin: '', token: 'T', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch }), seen };
}

const checked = { ...(fixture.documents as unknown as Record<string, Record<string, unknown>>).base, evidence_source: 'executed_structure_check' };

/** A gateway that stores uploads (content addressed), checks structures and creates training runs. */
function gateway(over: Partial<Record<'upload' | 'check' | 'train', (c: Seen) => { status: number; json: unknown } | Promise<{ status: number; json: unknown }>>> = {}) {
  return recorder(async (call) => {
    if (call.path.startsWith('/api/v1/uploads/model-config')) {
      if (over.upload) return over.upload(call);
      return { status: 200, json: fixtureUploadResponse(new Uint8Array(await (call.raw as Blob).arrayBuffer())) };
    }
    if (call.path === '/api/v1/structure-check') {
      if (over.check) return over.check(call);
      // 1.5.0: a user structure check reports the sha256 of the bytes it checked (here: the stored upload)
      const id = (call.json as { upload_id?: string }).upload_id;
      return { status: 200, json: { ...checked, source_sha256: id ? id.slice(4) : null } };
    }
    if (call.path === '/api/v1/training-runs') {
      if (over.train) return over.train(call);
      const src = (call.json as { source: Record<string, unknown> }).source;
      return { status: 200, json: { run_id: 'synthetic-training-run-0001', status: 'QUEUED', protocol_hash: 'sha256:' + 'a'.repeat(64), created: true, candidate_id: 'synthetic-cand-user', mode: 'scratch', source: { kind: 'user', model_name: src.model_name ?? null, source_sha256: src.upload_id ? `sha256:${String(src.upload_id).slice(4)}` : null } } };
    }
    return { status: 404, json: { code: 'NOT_FOUND', message: 'unmocked' } };
  });
}

const yamlFile = (text: string, name = 'mine.yaml'): File => new File([text], name, { type: 'application/x-yaml' });

async function chooseUser(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.selectOptions(screen.getByLabelText('모델 출처'), 'user');
}

describe('client pre-check of the upload (the server stays authoritative)', () => {
  it('accepts .yaml / .yml up to 1 MiB and names the code it would refuse with', () => {
    expect(precheckModelFile({ name: 'a.yaml', size: 1 })).toBeNull();
    expect(precheckModelFile({ name: 'A.YML', size: MODEL_CONFIG_MAX_BYTES })).toBeNull();
    expect(precheckModelFile({ name: 'a.txt', size: 10 })?.code).toBe('UPLOAD_TYPE_UNSUPPORTED');
    expect(precheckModelFile({ name: 'a.yaml.txt', size: 10 })?.code).toBe('UPLOAD_TYPE_UNSUPPORTED');
    expect(precheckModelFile({ name: 'a.yaml', size: 0 })?.code).toBe('UPLOAD_EMPTY');
    expect(precheckModelFile({ name: 'a.yaml', size: MODEL_CONFIG_MAX_BYTES + 1 })?.code).toBe('PAYLOAD_TOO_LARGE');
  });
  it('server errors are shown with their code, the Korean hint and the location', () => {
    const e = new ApiError('http', 'model config larger than 1048576 bytes', 413, { code: 'PAYLOAD_TOO_LARGE', message: 'model config larger than 1048576 bytes', location: 'gateway' });
    expect(uploadErrorText(e)).toBe('PAYLOAD_TOO_LARGE: model config larger than 1048576 bytes — 파일이 업로드 한도(기본 1 MiB)를 넘습니다. (location gateway)');
  });
});

describe('TrainingPanel upload flow (F5a: upload -> upload_id -> structure-check / training-runs)', () => {
  it('uploads the picked bytes unchanged (raw body, file name in ?filename=) and shows upload_id, source_sha256 and the browser sha256 match', async () => {
    const { client, seen } = gateway();
    const user = userEvent.setup();
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    // the 1.5.0 default is the upload: no server path field, and nothing to train before a file is uploaded
    expect(screen.queryByLabelText(/모델 구성 YAML 서버 절대경로/)).toBeNull();
    expect(screen.getByTestId('training-submit')).toBeDisabled();
    expect(screen.getByTestId('upload-required')).toBeInTheDocument();
    const name = '사용자 모델 v2.yaml';
    await user.upload(screen.getByTestId('model-upload-input'), yamlFile(ORIGINAL_YAML, name));
    const result = await screen.findByTestId('model-upload-result');
    const hex = sha256Hex(ORIGINAL_YAML);
    expect(within(result).getByTestId('upload-id')).toHaveTextContent(`upl-${hex}`);
    expect(within(result).getByTestId('upload-sha256')).toHaveTextContent(`sha256:${hex}`);
    expect(within(result).getByTestId('upload-local-sha256')).toHaveTextContent(hex);
    expect(within(result).getByTestId('upload-verdict')).toHaveTextContent('일치');
    expect(within(result).getByTestId('upload-bytes')).toHaveTextContent(`${Buffer.byteLength(ORIGINAL_YAML)} bytes`);
    expect(within(result).getByTestId('upload-name')).toHaveTextContent(name);
    const up = seen.filter((c) => c.path.startsWith('/api/v1/uploads/model-config'));
    expect(up).toHaveLength(1);
    expect(up[0].method).toBe('POST');
    expect(up[0].path).toBe(`/api/v1/uploads/model-config?filename=${encodeURIComponent(name)}`);
    expect(up[0].headers['Content-Type']).toBe('application/x-yaml');
    expect(up[0].headers.Authorization).toBe('Bearer T');
    expect(up[0].json).toBeUndefined();
    expect(await (up[0].raw as Blob).text()).toBe(ORIGINAL_YAML); // the exact bytes, not a JSON or form wrapping
    expect(screen.getByTestId('training-submit')).toBeEnabled();
  });

  it('the structure check and the training request carry upload_id + model_name and never a model_path', async () => {
    const { client, seen } = gateway();
    const user = userEvent.setup();
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    await user.upload(screen.getByTestId('model-upload-input'), yamlFile(ORIGINAL_YAML, 'mine.yml'));
    await screen.findByTestId('model-upload-result');
    const uploadId = `upl-${sha256Hex(ORIGINAL_YAML)}`;
    // 구조 상세 -> 서버 CPU 구조 검사 of the uploaded bytes
    await user.click(screen.getByTestId('registry-structure-open'));
    expect(screen.getByTestId('registry-structure-subject')).toHaveTextContent(`mine.yml · ${uploadId}`);
    await user.type(screen.getByTestId('registry-structure-size'), '64');
    await user.type(screen.getByTestId('registry-structure-nc'), '3');
    await user.click(screen.getByTestId('registry-structure-check'));
    await screen.findByTestId('registry-structure-result');
    const check = seen.filter((c) => c.path === '/api/v1/structure-check');
    expect(check.map((c) => c.json)).toEqual([{ upload_id: uploadId, model_name: 'mine.yml', input_size: 64, class_count: 3 }]);
    expect(screen.getByTestId('structure-source-sha256')).toHaveTextContent(sha256Hex(ORIGINAL_YAML));
    expect(screen.getByTestId('structure-source-verdict')).toHaveTextContent('일치 · 검사한 바이트 = 업로드한 바이트');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // the training run of the uploaded model
    await user.click(screen.getByTestId('training-submit'));
    const banner = await screen.findByTestId('training-source-sha');
    const train = seen.filter((c) => c.path === '/api/v1/training-runs');
    expect(train).toHaveLength(1);
    const body = train[0].json as { source: Record<string, unknown>; idempotency_key: string; mode: string };
    expect(body.source).toEqual({ kind: 'user', upload_id: uploadId, model_name: 'mine.yml' });
    expect(JSON.stringify(body)).not.toContain('model_path');
    expect(train[0].headers['Idempotency-Key']).toBe(body.idempotency_key);
    expect(banner).toHaveTextContent(`mine.yml source_sha256 sha256:${sha256Hex(ORIGINAL_YAML)}`);
  });

  it('advanced server path: model_path only behind the toggle, exclusive with the upload', async () => {
    const { client, seen } = gateway();
    const user = userEvent.setup();
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    await user.upload(screen.getByTestId('model-upload-input'), yamlFile(ORIGINAL_YAML));
    await screen.findByTestId('model-upload-result');
    await user.click(screen.getByTestId('model-path-advanced'));
    // the upload area is gone, the path field is there and empty: nothing can be submitted yet
    expect(screen.queryByTestId('model-upload')).toBeNull();
    expect(screen.getByTestId('training-submit')).toBeDisabled();
    await user.type(screen.getByLabelText(/모델 구성 YAML 서버 절대경로/), '/srv/models/mine.yaml');
    await user.click(screen.getByTestId('training-submit'));
    await waitFor(() => expect(seen.filter((c) => c.path === '/api/v1/training-runs')).toHaveLength(1));
    const source = (seen.find((c) => c.path === '/api/v1/training-runs')?.json as { source: Record<string, unknown> }).source;
    expect(source).toEqual({ kind: 'user', model_path: '/srv/models/mine.yaml' });
    // back to the default: the path field disappears and the upload area returns (the earlier upload was cleared)
    await user.click(screen.getByTestId('model-path-advanced'));
    expect(screen.queryByLabelText(/모델 구성 YAML 서버 절대경로/)).toBeNull();
    expect(screen.getByTestId('model-upload')).toBeInTheDocument();
    expect(screen.getByTestId('training-submit')).toBeDisabled();
  });

  it('client pre-check: a non-YAML name, an empty file or more than 1 MiB is refused without a request', async () => {
    const { client, seen } = gateway();
    const user = userEvent.setup({ applyAccept: false });
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    const input = screen.getByTestId('model-upload-input');
    const cases: [File, string][] = [
      [new File(['nc: 2\n'], 'model.txt'), 'UPLOAD_TYPE_UNSUPPORTED'],
      [new File([''], 'empty.yaml'), 'UPLOAD_EMPTY'],
      [new File(['a'.repeat(MODEL_CONFIG_MAX_BYTES + 1)], 'big.yaml'), 'PAYLOAD_TOO_LARGE'],
    ];
    for (const [file, code] of cases) {
      await user.upload(input, file);
      const err = await screen.findByTestId('model-upload-error');
      expect(err).toHaveAttribute('data-code', code);
      expect(err).toHaveTextContent('전송하지 않음');
    }
    expect(seen.filter((c) => c.path.startsWith('/api/v1/uploads'))).toHaveLength(0);
    expect(screen.queryByTestId('model-upload-result')).toBeNull();
  });

  it.each([
    [413, 'PAYLOAD_TOO_LARGE', 'gateway', '업로드 한도'],
    [422, 'UPLOAD_TYPE_UNSUPPORTED', 'gateway', '.yaml 또는 .yml'],
    [422, 'UPLOAD_NOT_UTF8', 'gateway', 'UTF-8'],
    [507, 'STORE_UNAVAILABLE', 'store', '저장소'],
  ])('a server rejection %i %s is shown by its code (not guessed from the status)', async (status, code, location, hint) => {
    const { client } = gateway({ upload: () => ({ status, json: { code, message: `synthetic ${code}`, location, request_id: 'req-1' } }) });
    const user = userEvent.setup();
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    await user.upload(screen.getByTestId('model-upload-input'), yamlFile(ORIGINAL_YAML));
    const err = await screen.findByTestId('model-upload-error');
    expect(err).toHaveAttribute('data-code', code);
    expect(err).toHaveTextContent(`${code}: synthetic ${code}`);
    expect(err).toHaveTextContent(hint);
    expect(err).toHaveTextContent(`location ${location}`);
    expect(screen.getByTestId('training-submit')).toBeDisabled();
  });

  it('a structure check that reports other bytes than the upload is flagged', async () => {
    const { client } = gateway({ check: () => ({ status: 200, json: { ...checked, source_sha256: 'f'.repeat(64) } }) });
    const user = userEvent.setup();
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    await user.upload(screen.getByTestId('model-upload-input'), yamlFile(ORIGINAL_YAML));
    await screen.findByTestId('model-upload-result');
    await user.click(screen.getByTestId('registry-structure-open'));
    await user.click(screen.getByTestId('registry-structure-check'));
    expect(await screen.findByTestId('structure-source-verdict')).toHaveTextContent('불일치');
  });

  it('a server sha256 different from the bytes the browser hashed is reported as a mismatch', async () => {
    const { client } = gateway({ upload: () => ({ status: 200, json: { upload_id: `upl-${'0'.repeat(64)}`, source_sha256: `sha256:${'0'.repeat(64)}`, bytes: 10 } }) });
    const user = userEvent.setup();
    render(<TrainingPanel client={client} protocol={fixtureDesignExampleProtocol()} />);
    await chooseUser(user);
    await user.upload(screen.getByTestId('model-upload-input'), yamlFile(ORIGINAL_YAML));
    expect(await screen.findByTestId('upload-verdict')).toHaveTextContent('불일치');
  });
});
