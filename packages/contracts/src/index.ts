import { Ajv2020 } from 'ajv/dist/2020.js';
import addFormatsModule from 'ajv-formats';
import type { ContractTypes } from './types.ts';
import { contractNames, schemaFor } from './schemas.ts';
export * from './types.ts';
export * from './schemas.ts';

const ajv = new Ajv2020({ allErrors: true, strict: true, allowUnionTypes: true });
// CommonJS interop differs between NodeNext and browser bundlers.
const addFormats = addFormatsModule as unknown as (instance: Ajv2020) => void;
addFormats(ajv);
const validators = Object.fromEntries(contractNames.map(name => [name, ajv.compile(schemaFor(name))]));
export class ContractError extends Error {
  constructor(public readonly contract: string, public readonly details: string) { super(`Invalid ${contract}: ${details}`); this.name = 'ContractError'; }
}
export function parseContract<K extends keyof ContractTypes>(name: K, value: unknown): ContractTypes[K] {
  const validate = validators[name];
  if (!validate || !validate(value)) throw new ContractError(name, ajv.errorsText(validate?.errors, { separator: '; ' }));
  return structuredClone(value) as ContractTypes[K];
}
