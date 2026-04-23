"""
Generate YouTube metadata (title, description, tags) from a story text file
using Cloudflare Workers AI.

Usage:
    python generate_metadata.py <story_file>

Example:
    python generate_metadata.py story.txt

Requires environment variables:
    CLOUDFLARE_ACCOUNT_ID
    CLOUDFLARE_API_TOKEN
"""

import os
import sys
import json
import re
import requests


def generate_metadata(story_content: str, account_id: str, api_token: str) -> dict:
    """Call Cloudflare Workers AI to generate YouTube metadata from story text."""
    model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    system_prompt = "คุณเป็นผู้ช่วย AI ที่เชี่ยวชาญในการสร้าง metadata สำหรับวิดีโอ YouTube ในรูปแบบ JSON ตอบเป็น JSON เท่านั้น"
    user_prompt = f"""จากเรื่องราวด้านล่างนี้ ให้สร้าง JSON object ที่ประกอบด้วย:
- "title": ชื่อวิดีโอที่น่าสนใจ ดึงดูดผู้ชม (ไม่เกิน 80 ตัวอักษร) ต้องลงท้ายด้วย " | ฟังธรรมะก่อนนอน" เสมอ
- "description": คำอธิบายวิดีโอแบบละเอียด ยาว 3-5 ย่อหน้า ครอบคลุมเนื้อหาสำคัญทั้งหมดของเรื่องราว รวมถึงบทเรียน ข้อคิด และแก่นธรรมที่ได้จากเรื่อง เขียนให้น่าอ่านและดึงดูดให้คนอยากดูวิดีโอ
- "tags": อาเรย์ของ 5-8 คำสำคัญ (keywords) ที่เกี่ยวข้องกับเรื่องราว โดยใส่ # นำหน้าทุกคำ

ตอบเป็น raw JSON object เท่านั้น ไม่ต้องมี markdown ไม่ต้องมีคำอธิบายเพิ่มเติม
ตัวอย่าง:
{{
  "title": "ผู้แผ่เมตตา จะได้รับคุณแห่งเมตตาด้วยตนเองก่อน | ฟังธรรมะก่อนนอน",
  "description": "เรื่องราวนี้สอนให้เราเห็นถึงพลังของการแผ่เมตตา ซึ่งเป็นหนึ่งในพรหมวิหาร 4 ที่พระพุทธเจ้าทรงสอนไว้\\n\\nการแผ่เมตตาไม่ใช่เพียงการส่งความปรารถนาดีไปยังผู้อื่น แต่ยังเป็นการปลูกฝังจิตใจของเราเองให้เต็มเปี่ยมไปด้วยความรักความเมตตา ผู้ที่แผ่เมตตาเป็นประจำจะพบว่าจิตใจสงบ นอนหลับเป็นสุข และมีสัมพันธภาพที่ดีกับคนรอบข้าง\\n\\nแก่นธรรมสำคัญคือ เมื่อเราให้เมตตาแก่ผู้อื่น เราเองจะเป็นผู้ได้รับผลแห่งเมตตานั้นก่อนใครเพื่อน จึงเป็นบทเรียนที่สอนว่า การให้คือการได้รับที่แท้จริง",
  "tags": ["#ธรรมะ", "#ข้อคิด", "#เรื่องเล่า", "#ฟังธรรมะก่อนนอน", "#ธรรมะสอนใจ", "#พรหมวิหาร", "#แผ่เมตตา", "#สมาธิ"]
}}

เรื่องราว:
{story_content}"""

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2048,
    }

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()

    data = response.json()

    if not data.get("success", False):
        raise RuntimeError(f"API Error: {data.get('errors')}")

    result = data.get("result", {})

    # Handle multiple response formats:
    # 1. response is already a dict (structured output): {"result": {"response": {...}}}
    # 2. response is a string (text): {"result": {"response": "..."}}
    # 3. OpenAI-compatible: {"result": {"choices": [{"message": {"content": "..."}}]}}
    response = result.get("response") if isinstance(result, dict) else result

    # Case 1: Already a dict with the metadata fields
    if isinstance(response, dict) and "title" in response:
        if "tags" not in response:
            response["tags"] = ["#ธรรมะ", "#ข้อคิด", "#เรื่องเล่า"]
        return ensure_title_suffix(response)

    # Case 2: String that needs JSON parsing
    if isinstance(response, str) and response:
        return ensure_title_suffix(parse_metadata(response))

    # Case 3: OpenAI-compatible choices format
    if isinstance(result, dict) and "choices" in result:
        choices = result["choices"]
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return ensure_title_suffix(parse_metadata(content))

    raise RuntimeError(
        f"Empty or unexpected response from API. Result: {result}")


TITLE_SUFFIX         = " | นิทานธรรมะก่อนนอน"
LOTTERY_TITLE_SUFFIX = " | วิเคราะห์หวยลาว"


def generate_lottery_metadata(script_content: str, account_id: str, api_token: str) -> dict:
    """Generate YouTube metadata tailored for a Lao lottery prediction video."""
    model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    url   = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    system_prompt = "คุณเป็นผู้ช่วย AI ที่เชี่ยวชาญในการสร้าง metadata สำหรับวิดีโอ YouTube หวยลาว ตอบเป็น JSON เท่านั้น"
    user_prompt = f"""จากสคริปต์วิดีโอวิเคราะห์หวยลาวด้านล่างนี้ ให้สร้าง JSON object ที่ประกอบด้วย:
- "title": ชื่อวิดีโอที่น่าสนใจ ดึงดูด ระบุงวดที่ทำนาย (ไม่เกิน 80 ตัวอักษร) ต้องลงท้ายด้วย "{LOTTERY_TITLE_SUFFIX}" เสมอ
- "description": คำอธิบายวิดีโอ 3-4 ย่อหน้า ครอบคลุม: งวดที่ทำนาย, วิธีวิเคราะห์สถิติ, เลขเด็ดที่แนะนำ, คำเตือนว่าเป็นการวิเคราะห์สถิติเพื่อความบันเทิงเท่านั้น
- "tags": อาเรย์ของ 8-10 คำสำคัญ ใส่ # นำหน้า เช่น #หวยลาว #เลขเด็ด #วิเคราะห์หวย

ตอบเป็น raw JSON object เท่านั้น ไม่ต้องมี markdown ไม่ต้องมีคำอธิบายเพิ่มเติม
ตัวอย่าง:
{{
  "title": "เลขเด็ดหวยลาว งวด 20 เมษายน 2569 วิเคราะห์สถิติ{LOTTERY_TITLE_SUFFIX}",
  "description": "วิเคราะห์สถิติหวยลาวงวดนี้อย่างละเอียด...",
  "tags": ["#หวยลาว", "#เลขเด็ด", "#วิเคราะห์หวย", "#หวยลาวพัฒนา", "#สถิติหวย", "#เลขเด็ดลาว", "#ทำนายเลข", "#หวยงวดนี้"]
}}

สคริปต์วิดีโอ:
{script_content[:3000]}"""

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type":  "application/json",
    }

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()

    if not data.get("success", False):
        raise RuntimeError(f"API Error: {data.get('errors')}")

    result   = data.get("result", {})
    raw      = result.get("response") if isinstance(result, dict) else result

    if isinstance(raw, dict) and "title" in raw:
        metadata = raw
    elif isinstance(raw, str):
        metadata = parse_metadata(raw)
    elif isinstance(result, dict) and "choices" in result:
        content  = result["choices"][0].get("message", {}).get("content", "")
        metadata = parse_metadata(content)
    else:
        raise RuntimeError(f"Unexpected response: {result}")

    # Enforce lottery suffix
    title = metadata.get("title", "เลขเด็ดหวยลาว")
    if not title.endswith(LOTTERY_TITLE_SUFFIX):
        title = title.rstrip().rstrip("|").rstrip()
        metadata["title"] = title + LOTTERY_TITLE_SUFFIX

    if "tags" not in metadata:
        metadata["tags"] = []

    # Always include these fixed tags in the tags field (for search algorithm)
    fixed = ["#หวยลาว", "#เลขเด็ด", "#หวยลาววันนี้"]
    existing = set(metadata["tags"])
    for tag in fixed:
        if tag not in existing:
            metadata["tags"].insert(0, tag)

    # Also append fixed hashtags to description so viewers can see + click them
    desc = metadata.get("description", "")
    hashtag_line = "\n\n" + "  ".join(fixed)
    if fixed[0] not in desc:
        metadata["description"] = desc + hashtag_line

    return metadata


def ensure_title_suffix(metadata: dict) -> dict:
    """Ensure title always ends with the channel suffix."""
    title = metadata.get("title", "")
    if not title.endswith(TITLE_SUFFIX):
        # Remove trailing whitespace/pipe fragments before appending
        title = title.rstrip().rstrip("|").rstrip()
        metadata["title"] = title + TITLE_SUFFIX
    return metadata


def _repair_truncated_json(text: str) -> str:
    """Attempt to close an incomplete JSON object caused by token truncation."""
    # Count unmatched braces/brackets to decide what to close
    depth_brace   = 0
    depth_bracket = 0
    in_string     = False
    escape_next   = False

    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace -= 1
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket -= 1

    suffix = ""
    # If we're still inside a string, close it
    if in_string:
        suffix += '"'
    # Close open arrays and objects
    suffix += ']' * depth_bracket
    suffix += '}' * depth_brace
    return text + suffix


def parse_metadata(content: str) -> dict:
    """Parse JSON metadata from model response text."""
    # Strip markdown code fences if present
    content = re.sub(r"```json\s*", "", content)
    content = re.sub(r"```\s*", "", content)

    # Try to extract JSON object (greedy so we grab the outermost {})
    match = re.search(r"\{.*\}", content, re.DOTALL)
    raw_json = match.group(0) if match else None

    # If no closing brace found, try from the first '{' to end of string
    if raw_json is None:
        start = content.find('{')
        if start != -1:
            raw_json = content[start:]

    if raw_json:
        # First attempt: parse as-is
        try:
            metadata = json.loads(raw_json)
            if "title" in metadata and "description" in metadata:
                if "tags" not in metadata:
                    metadata["tags"] = []
                return metadata
        except json.JSONDecodeError:
            pass

        # Second attempt: repair truncated JSON then parse
        try:
            repaired = _repair_truncated_json(raw_json)
            metadata = json.loads(repaired)
            if "title" in metadata and "description" in metadata:
                if "tags" not in metadata:
                    metadata["tags"] = []
                return metadata
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse metadata from response: {content[:300]}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_metadata.py <story_file>")
        sys.exit(1)

    story_path = sys.argv[1]

    if not os.path.exists(story_path):
        print(f"Error: Story file '{story_path}' not found.")
        sys.exit(1)

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")

    # account_id = os.getenv(
    #     "CLOUDFLARE_ACCOUNT_ID") or "2709cc1c8dbb4760d0a597889419bc64"
    # api_token = os.getenv(
    #     "CLOUDFLARE_API_TOKEN") or "3LIBINMeJ60jg-zUARuVSQJ2UuaiExFfdfJjk2L1"

    if not account_id or not api_token:
        print("Error: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables must be set.", file=sys.stderr)
        print("   Export them:", file=sys.stderr)
        print("     export CLOUDFLARE_ACCOUNT_ID=your_account_id", file=sys.stderr)
        print("     export CLOUDFLARE_API_TOKEN=your_api_token", file=sys.stderr)
        sys.exit(1)

    with open(story_path, "r", encoding="utf-8") as f:
        story_content = f.read()

    print(f"Story file: {story_path}")
    print(f"Text length: {len(story_content)} chars")
    print()
    is_lottery = os.path.basename(story_path).startswith("lottery_tts_")
    print(f"Generating {'lottery' if is_lottery else 'dharma'} metadata...")

    try:
        if is_lottery:
            metadata = generate_lottery_metadata(story_content, account_id, api_token)
        else:
            metadata = generate_metadata(story_content, account_id, api_token)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Save to generate/metadata.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, "metadata.json")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print()
    print(f"Metadata saved to: {output_file}")
    print()
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
