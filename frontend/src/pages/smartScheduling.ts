export const smartSchedulingAccountTypes = ["pro", "plus", "k12", "team"] as const;

export type SmartSchedulingAccountType = (typeof smartSchedulingAccountTypes)[number];

export type SmartSchedulingAccountRule = {
  manual_priority_min: number;
  manual_priority_max: number;
  system_priority_min: number;
  system_priority_max: number;
  automatic_priority: number;
  normal_concurrency: number;
  extreme_entry_percent: number;
  recovery_percent: number;
  extreme_concurrency: number;
};

export type SmartSchedulingRules = {
  account_types: Record<SmartSchedulingAccountType, SmartSchedulingAccountRule>;
  extreme: {
    priority_min: number;
    priority_max: number;
    priority: number;
  };
};

export type SmartSchedulingAccountRuleForm = {
  [Key in keyof SmartSchedulingAccountRule]: string;
};

export type SmartSchedulingForm = Record<SmartSchedulingAccountType, SmartSchedulingAccountRuleForm> & {
  extreme: {
    priority_min: string;
    priority_max: string;
    priority: string;
  };
};

export type SmartSchedulingPayloadResult =
  | { ok: true; payload: { rules: SmartSchedulingRules } }
  | { ok: false; error: string };

export const defaultSmartSchedulingRules: SmartSchedulingRules = {
  account_types: {
    pro: {
      manual_priority_min: 1000,
      manual_priority_max: 1090,
      system_priority_min: 991,
      system_priority_max: 999,
      automatic_priority: 991,
      normal_concurrency: 30,
      extreme_entry_percent: 95,
      recovery_percent: 80,
      extreme_concurrency: 100,
    },
    plus: {
      manual_priority_min: 200,
      manual_priority_max: 290,
      system_priority_min: 191,
      system_priority_max: 199,
      automatic_priority: 191,
      normal_concurrency: 30,
      extreme_entry_percent: 90,
      recovery_percent: 80,
      extreme_concurrency: 100,
    },
    k12: {
      manual_priority_min: 100,
      manual_priority_max: 190,
      system_priority_min: 91,
      system_priority_max: 99,
      automatic_priority: 91,
      normal_concurrency: 30,
      extreme_entry_percent: 90,
      recovery_percent: 80,
      extreme_concurrency: 100,
    },
    team: {
      manual_priority_min: 50,
      manual_priority_max: 90,
      system_priority_min: 41,
      system_priority_max: 49,
      automatic_priority: 41,
      normal_concurrency: 30,
      extreme_entry_percent: 90,
      recovery_percent: 80,
      extreme_concurrency: 100,
    },
  },
  extreme: {
    priority_min: 1,
    priority_max: 20,
    priority: 10,
  },
};

const accountRuleFields = [
  "manual_priority_min",
  "manual_priority_max",
  "system_priority_min",
  "system_priority_max",
  "automatic_priority",
  "normal_concurrency",
  "extreme_entry_percent",
  "recovery_percent",
  "extreme_concurrency",
] as const;

const integerFieldLabels: Partial<Record<keyof SmartSchedulingAccountRule, string>> = {
  manual_priority_min: "手动优先级下限",
  manual_priority_max: "手动优先级上限",
  system_priority_min: "系统优先级下限",
  system_priority_max: "系统优先级上限",
  automatic_priority: "自动优先级",
  normal_concurrency: "普通并发",
  extreme_concurrency: "极限并发",
};

export function smartSchedulingRulesToForm(rules: SmartSchedulingRules): SmartSchedulingForm {
  const accountForms = Object.fromEntries(
    smartSchedulingAccountTypes.map((accountType) => [
      accountType,
      Object.fromEntries(
        accountRuleFields.map((field) => [field, String(rules.account_types[accountType][field])]),
      ) as SmartSchedulingAccountRuleForm,
    ]),
  ) as Record<SmartSchedulingAccountType, SmartSchedulingAccountRuleForm>;

  return {
    ...accountForms,
    extreme: {
      priority_min: String(rules.extreme.priority_min),
      priority_max: String(rules.extreme.priority_max),
      priority: String(rules.extreme.priority),
    },
  };
}

export function buildSmartSchedulingPayload(form: SmartSchedulingForm): SmartSchedulingPayloadResult {
  const parsedRules = {} as Record<SmartSchedulingAccountType, SmartSchedulingAccountRule>;

  for (const accountType of smartSchedulingAccountTypes) {
    const source = form[accountType];
    const parsed = {} as SmartSchedulingAccountRule;
    for (const field of accountRuleFields) {
      const value = source[field];
      if (field === "extreme_entry_percent" || field === "recovery_percent") {
        const percent = parseNumber(value);
        if (percent === null || percent < 0 || percent > 100) {
          return { ok: false, error: `${accountType} 的百分比必须是 0 到 100 之间的数字` };
        }
        parsed[field] = percent;
        continue;
      }
      const integer = parseInteger(value);
      const maximum = field === "normal_concurrency" || field === "extreme_concurrency" ? 10_000 : 100_000;
      if (integer === null || integer < 1 || integer > maximum) {
        return { ok: false, error: `${accountType} 的${integerFieldLabels[field] || field}必须是有效正整数` };
      }
      parsed[field] = integer;
    }

    if (parsed.manual_priority_min > parsed.manual_priority_max) {
      return { ok: false, error: `${accountType} 的手动优先级区间上下限无效` };
    }
    if (parsed.system_priority_min > parsed.system_priority_max) {
      return { ok: false, error: `${accountType} 的系统优先级区间上下限无效` };
    }
    if (parsed.system_priority_max >= parsed.manual_priority_min) {
      return { ok: false, error: `${accountType} 的系统优先级区间必须完整位于手动区间之前` };
    }
    if (
      parsed.automatic_priority < parsed.system_priority_min
      || parsed.automatic_priority > parsed.system_priority_max
    ) {
      return { ok: false, error: `${accountType} 的自动优先级必须位于系统优先级区间内` };
    }
    if (parsed.recovery_percent >= parsed.extreme_entry_percent) {
      return { ok: false, error: `${accountType} 的恢复阈值必须低于极限加速阈值` };
    }
    parsedRules[accountType] = parsed;
  }

  const priorityMin = parseInteger(form.extreme.priority_min);
  const priorityMax = parseInteger(form.extreme.priority_max);
  const priority = parseInteger(form.extreme.priority);
  if (priorityMin === null || priorityMax === null || priority === null) {
    return { ok: false, error: "极限优先级区间和固定值必须是有效正整数" };
  }
  if (priorityMin < 1 || priorityMax > 100_000 || priorityMin > priorityMax) {
    return { ok: false, error: "极限优先级区间上下限无效" };
  }
  if (priority < priorityMin || priority > priorityMax) {
    return { ok: false, error: "极限固定优先级必须位于极限区间内" };
  }

  const intervals = smartSchedulingAccountTypes.flatMap((accountType) => {
    const rule = parsedRules[accountType];
    return [
      { min: rule.system_priority_min, max: rule.system_priority_max, label: `${accountType} 系统区间` },
      { min: rule.manual_priority_min, max: rule.manual_priority_max, label: `${accountType} 手动区间` },
    ];
  }).sort((left, right) => left.min - right.min);
  if (priorityMax >= intervals[0].min) {
    return { ok: false, error: "极限优先级区间必须位于所有普通区间之前" };
  }
  for (let index = 1; index < intervals.length; index += 1) {
    const previous = intervals[index - 1];
    const current = intervals[index];
    if (previous.max >= current.min) {
      return { ok: false, error: `优先级区间重叠：${previous.label} 与 ${current.label}` };
    }
  }

  return {
    ok: true,
    payload: {
      rules: {
        account_types: parsedRules,
        extreme: {
          priority_min: priorityMin,
          priority_max: priorityMax,
          priority,
        },
      },
    },
  };
}

function parseInteger(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function parseNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
