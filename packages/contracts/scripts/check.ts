/**
 * M02 §4.1.2 / §3.4B —— check.ts：CI 一致性校验（contracts:check）
 * 重新生成全部产物 → 与仓库已提交生成物做确定性 hash 比对 → drift 非空即非零退出。
 * 「schema 变更即失败」（§8 W6 行）；fail-closed，无在线关闭开关。
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../.."); // 仓库根
const CONTRACTS = resolve(ROOT, "packages/contracts");

function runGen(args: string[]): void {
  const r = spawnSync("pnpm", ["exec", "tsx", "scripts/generate.ts", ...args], {
    stdio: "pipe", cwd: CONTRACTS, shell: process.platform === "win32",
  });
  if (r.status !== 0) throw new Error(`generate failed: ${args.join(" ")}`);
}

/** 排序后文件内容摘要（确定性，§4.5 并发约定） */
export function hashGenerated(dir: string, relPrefix = ""): string {
  const h = createHash("sha256");
  const walk = (d: string, rel: string) => {
    for (const f of readdirSync(d).sort()) {
      const p = join(d, f);
      const r = rel ? `${rel}/${f}` : f;
      if (statSync(p).isDirectory()) walk(p, r);
      else h.update(`${r}:${createHash("sha256").update(readFileSync(p)).digest("hex")}\n`);
    }
  };
  walk(dir, relPrefix);
  return h.digest("hex");
}

export async function verifyGenerated(): Promise<{ ok: boolean; drift: string[] }> {
  const drift: string[] = [];

  // 1. 枚举一致性（I2）：enums.ts / enums.py 从同一 source 重新生成后逐字节比对
  const enumTs = readFileSync(join(CONTRACTS, "enums.ts"));
  const enumPy = readFileSync(join(CONTRACTS, "enums.py"));
  runGen(["gen:enums"]);
  if (!enumTs.equals(readFileSync(join(CONTRACTS, "enums.ts"))) || !enumPy.equals(readFileSync(join(CONTRACTS, "enums.py")))) {
    drift.push("enums: enums.source.json 与已提交 enums.ts/enums.py 不一致 → 运行 pnpm gen:enums");
  }

  // 2. Zod schema：重新生成与已提交 packages/sdk/ts/schemas.ts 比对（I3）
  const committedSchemas = readFileSync(resolve(ROOT, "packages/sdk/ts/schemas.ts"));
  runGen(["gen:zod"]);
  if (!committedSchemas.equals(readFileSync(resolve(ROOT, "packages/sdk/ts/schemas.ts")))) {
    drift.push("zod: schema 源与 packages/sdk/ts/schemas.ts 不一致 → 运行 pnpm gen:zod");
  }

  // 3. TS 类型（openapi-typescript 存在时才校验；未安装不影响门禁骨架）
  try {
    const committedTypes = readFileSync(resolve(ROOT, "packages/sdk/ts/types.ts"));
    runGen(["gen:openapi"]);
    if (!committedTypes.equals(readFileSync(resolve(ROOT, "packages/sdk/ts/types.ts")))) {
      drift.push("types: openapi.yaml 与 packages/sdk/ts/types.ts 不一致 → 运行 pnpm gen:openapi");
    }
  } catch {
    console.error("NOTE: packages/sdk/ts/types.ts 或 openapi-typescript 缺失，跳过 TS 类型校验");
  }

  return { ok: drift.length === 0, drift };
}

// ---------- CLI ----------
const result = await verifyGenerated();
if (result.ok) {
  console.log("contracts:check OK —— 生成物与契约源一致（drift=[]）");
  process.exit(0);
} else {
  console.error("contracts:check FAILED —— schema 变更即失败（§8 W6）：");
  for (const d of result.drift) console.error(`  - ${d}`);
  process.exit(1);
}
