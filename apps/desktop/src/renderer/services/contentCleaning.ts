const PAGE_LINE_RE =
  /^(?:page\s*\d+|\d+\s*\/\s*\d+|第\s*\d+\s*页|[-–—]?\s*\d+\s*[-–—]?)$/i;
const COPYRIGHT_RE =
  /^(?:copyright\b|all rights reserved|\(c\)|©|转载|免责声明|版权所有)/i;
const URL_ONLY_RE = /^https?:\/\/\S+$/i;
const NOISE_LINE_RE =
  /^(?:导航|返回|首页|上一[篇页]|下一[篇页]|相关文章|相关推荐|分享|收藏|点赞|登录|注册|目录|菜单|打开应用|下载app|download app)(?:\s*[|/·•-]\s*.*)?$/i;
const MENU_SEPARATOR_RE = /^[^.!?。！？:：]{1,18}(?:\s*[|/·•-]\s*[^.!?。！？:：]{1,18}){1,4}$/;
const HEADING_RE = /^(?:#{1,6}\s+.+|第[一二三四五六七八九十0-9]+[章节部分篇].*|[A-Z][A-Z0-9\s_-]{2,})$/;
const BULLET_RE = /^(?:[-*•]\s+|\d+[.)、]\s+|[一二三四五六七八九十]+[、.]\s+)/;
const SENTENCE_END_RE = /[。！？.!?;；:：)]$/;
const JOINABLE_START_RE = /^[a-z0-9\u4e00-\u9fff(（"“]/i;
const OCR_REPEAT_NORMALIZE_RE = /\s+/g;

const normalizeLines = (content: string) =>
  content
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/\u00a0/g, " ")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim());

const collapseBlankLines = (lines: string[]) => {
  const result: string[] = [];
  let previousBlank = false;

  for (const line of lines) {
    const isBlank = line.length === 0;
    if (isBlank && previousBlank) {
      continue;
    }
    result.push(line);
    previousBlank = isBlank;
  }

  while (result[0] === "") {
    result.shift();
  }
  while (result[result.length - 1] === "") {
    result.pop();
  }

  return result;
};

const isObviousNoiseLine = (line: string) =>
  PAGE_LINE_RE.test(line) ||
  COPYRIGHT_RE.test(line) ||
  URL_ONLY_RE.test(line) ||
  MENU_SEPARATOR_RE.test(line) ||
  NOISE_LINE_RE.test(line);

const dedupeConsecutiveLines = (lines: string[]) => {
  const result: string[] = [];

  for (const line of lines) {
    if (!line) {
      result.push(line);
      continue;
    }

    const previous = result[result.length - 1];
    const normalizedLine = line.replace(OCR_REPEAT_NORMALIZE_RE, "");
    const normalizedPrevious = previous?.replace(OCR_REPEAT_NORMALIZE_RE, "") ?? null;
    if (normalizedPrevious && normalizedPrevious === normalizedLine) {
      continue;
    }
    result.push(line);
  }

  return result;
};

const shouldKeepParagraphBreak = (current: string, next: string) => {
  if (!current || !next) {
    return true;
  }
  if (HEADING_RE.test(current) || HEADING_RE.test(next)) {
    return true;
  }
  if (BULLET_RE.test(current) || BULLET_RE.test(next)) {
    return true;
  }
  if (SENTENCE_END_RE.test(current)) {
    return true;
  }
  if (!JOINABLE_START_RE.test(next)) {
    return true;
  }
  return false;
};

const mergeWrappedLines = (lines: string[]) => {
  const result: string[] = [];

  for (const line of lines) {
    if (!line) {
      if (result[result.length - 1] !== "") {
        result.push("");
      }
      continue;
    }

    const previous = result[result.length - 1];
    if (!previous || previous === "") {
      result.push(line);
      continue;
    }

    if (shouldKeepParagraphBreak(previous, line)) {
      result.push(line);
      continue;
    }

    result[result.length - 1] = `${previous}${line}`;
  }

  return result;
};

export const applyBasicContentCleaning = (content: string) => {
  const lines = normalizeLines(content).filter((line) => {
    if (!line) {
      return true;
    }
    return !isObviousNoiseLine(line);
  });

  return collapseBlankLines(lines).join("\n");
};

export const applyEnhancedContentCleaning = (content: string) => {
  const basicLines = applyBasicContentCleaning(content).split("\n");
  const dedupedLines = dedupeConsecutiveLines(basicLines);
  const mergedLines = mergeWrappedLines(dedupedLines);

  return collapseBlankLines(mergedLines).join("\n");
};
