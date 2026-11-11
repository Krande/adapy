// The start form's parameter section when no plugin supplies one: one plain input per property
// of the provider's JSON Schema (`@/services/simulations/schemaForm`). Strings and numbers are
// text and number inputs, booleans a checkbox, enums a select; an object or array property is a
// text box holding JSON. The server checks what is sent, so this does not validate.

import React from "react";

import type { ParameterSchema } from "@/services/api/simulations";
import {
  fieldInputValue,
  parseFieldInput,
  schemaFields,
  withParameter,
  type SchemaField,
} from "@/services/simulations/schemaForm";

const INPUT = "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 flex-1 min-w-0";

export interface SchemaFormProps {
  schema: ParameterSchema;
  value: Readonly<Record<string, unknown>>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
}

const FieldInput: React.FC<{
  field: SchemaField;
  value: unknown;
  disabled?: boolean;
  onValue: (v: unknown) => void;
}> = ({ field, value, disabled, onValue }) => {
  if (field.kind === "boolean") {
    return (
      <input
        type="checkbox"
        checked={value === true}
        disabled={disabled}
        onChange={(e) => onValue(parseFieldInput(field, e.target.checked))}
      />
    );
  }
  if (field.kind === "enum") {
    return (
      <select
        className={INPUT}
        value={fieldInputValue(field, value)}
        disabled={disabled}
        onChange={(e) => onValue(parseFieldInput(field, e.target.value))}
      >
        {/* Nothing chosen: an optional choice may stay so, a required one has to be made. */}
        <option value="-1">{field.required ? "(choose)" : "(not set)"}</option>
        {(field.options ?? []).map((o, i) => (
          <option key={i} value={String(i)}>
            {String(o)}
          </option>
        ))}
      </select>
    );
  }
  const numeric = field.kind === "number" || field.kind === "integer";
  return (
    <input
      type={numeric ? "number" : "text"}
      className={INPUT}
      value={fieldInputValue(field, value)}
      min={field.minimum}
      max={field.maximum}
      step={field.kind === "integer" ? 1 : "any"}
      placeholder={field.kind === "json" ? "JSON" : undefined}
      disabled={disabled}
      onChange={(e) => onValue(parseFieldInput(field, e.target.value))}
    />
  );
};

/** A provider's parameters as plain inputs. Renders a note when the schema has no properties. */
export const SchemaForm: React.FC<SchemaFormProps> = ({ schema, value, onChange, disabled }) => {
  const fields = schemaFields(schema);
  if (fields.length === 0) return <div className="text-gray-400">This provider takes no parameters.</div>;
  return (
    <div className="flex flex-col gap-1">
      {fields.map((field) => (
        <label key={field.name} className="flex items-center gap-2" title={field.description}>
          <span className="text-gray-300 w-28 shrink-0 truncate">
            {field.label}
            {field.required ? <span className="text-red-300"> *</span> : null}
          </span>
          <FieldInput
            field={field}
            value={value[field.name]}
            disabled={disabled}
            onValue={(v) => onChange(withParameter(value, field.name, v))}
          />
        </label>
      ))}
    </div>
  );
};

export default SchemaForm;
