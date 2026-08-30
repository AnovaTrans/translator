import xml.etree.ElementTree as ET
from models.entities import TranslationSegment
from typing import List, Dict, Tuple, Optional
from services.language_codes import normalize_lang_code
import re
import copy

# Known XLIFF namespaces
_XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
_MQ_NS = "MQXliff"
_SDL_NS = "http://sdl.com/FileTypes/SdlXliff/1.0"


class XMLParser:

    @staticmethod
    def detect_languages(content: bytes) -> Tuple[Optional[str], Optional[str]]:
        """Detect source and target languages from any XLIFF variant."""
        try:
            xml_str = None
            for enc in ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1']:
                try:
                    xml_str = content.decode(enc)
                    break
                except Exception:
                    continue

            if not xml_str:
                return None, None

            root = ET.fromstring(xml_str)

            # Try XLIFF 1.2 namespace first, then plain
            file_elem = root.find(f'.//{{{_XLIFF_NS}}}file')
            if file_elem is None:
                file_elem = root.find('.//file')
            if file_elem is None:
                file_elem = root

            source_lang = file_elem.get('source-language')
            target_lang = file_elem.get('target-language')

            # Normalize any code format (memoQ 3-letter, SDL BCP-47, plain 2-letter)
            if source_lang:
                source_lang = normalize_lang_code(source_lang)
            if target_lang:
                target_lang = normalize_lang_code(target_lang)

            return source_lang, target_lang

        except Exception as e:
            print(f"Language detection error: {e}")
            return None, None

    @staticmethod
    def _extract_text_with_tags(element) -> Tuple[str, Dict[str, ET.Element]]:
        """
        Converts an XML element's mixed content into a plain string with
        {{n}} placeholders for all inline child tags.
        Returns (text_with_placeholders, {placeholder: element_copy})
        """
        tag_map: Dict[str, ET.Element] = {}
        tag_counter = 1
        text_content = element.text if element.text else ""

        for child in list(element):
            placeholder = f"{{{{{tag_counter}}}}}"
            tag_copy = copy.deepcopy(child)
            tag_copy.tail = None
            tag_map[placeholder] = tag_copy
            text_content += placeholder
            if child.tail:
                text_content += child.tail
            tag_counter += 1

        return text_content, tag_map

    @staticmethod
    def _reconstruct_element(target_element: ET.Element, translated_text: str,
                              tag_map: Dict[str, ET.Element]):
        """
        Rebuilds XML from a string with {{n}} placeholders.
        Populates target_element with text and child nodes.
        """
        parts = re.split(r'(\{\{\d+\}\})', translated_text)
        last_element = None

        for part in parts:
            if not part:
                continue
            if re.match(r'^\{\{\d+\}\}$', part):
                if part in tag_map:
                    new_tag = copy.deepcopy(tag_map[part])
                    new_tag.tail = ""
                    target_element.append(new_tag)
                    last_element = new_tag
                else:
                    # Hallucinated placeholder — treat as literal text
                    if last_element is not None:
                        last_element.tail = (last_element.tail or "") + part
                    else:
                        target_element.text = (target_element.text or "") + part
            else:
                if last_element is not None:
                    last_element.tail = (last_element.tail or "") + part
                else:
                    target_element.text = (target_element.text or "") + part

    @staticmethod
    def _register_namespaces():
        ET.register_namespace('', _XLIFF_NS)
        ET.register_namespace('mq', _MQ_NS)
        ET.register_namespace('sdl', _SDL_NS)

    @staticmethod
    def parse_xliff(content: bytes) -> List[TranslationSegment]:
        """Parse any XLIFF variant (standard, mqxliff, sdlxliff) into segments."""
        try:
            XMLParser._register_namespaces()

            xml_str = None
            for enc in ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1']:
                try:
                    xml_str = content.decode(enc)
                    break
                except Exception:
                    continue

            if not xml_str:
                return []

            root = ET.fromstring(xml_str)
            segments: List[TranslationSegment] = []
            ns = {'x': _XLIFF_NS, 'mq': _MQ_NS, 'sdl': _SDL_NS}

            # Find all trans-unit elements
            trans_units = root.findall(".//x:trans-unit", ns)
            if not trans_units:
                trans_units = root.findall(".//trans-unit")

            for trans_unit in trans_units:
                seg_id = trans_unit.get('id')
                if not seg_id:
                    continue

                source_node = trans_unit.find("x:source", ns)
                if source_node is None:
                    source_node = trans_unit.find("source")

                target_node = trans_unit.find("x:target", ns)
                if target_node is None:
                    target_node = trans_unit.find("target")

                if source_node is None:
                    continue

                source_text, tag_map = XMLParser._extract_text_with_tags(source_node)
                target_text = ""
                if target_node is not None:
                    target_text = "".join(target_node.itertext())

                segments.append(TranslationSegment(
                    id=seg_id,
                    source=source_text,
                    target=target_text,
                    tag_map=tag_map
                ))

            return segments

        except Exception as e:
            print(f"XLIFF Parsing Error: {e}")
            return []

    @staticmethod
    def update_xliff(original_content: bytes, translations: Dict[str, str],
                     segments_map: Dict[str, TranslationSegment]) -> bytes:
        """
        Updates XLIFF with translated target texts, preserving all original
        structure, namespaces, and MemoQ/SDL metadata attributes.
        """
        XMLParser._register_namespaces()

        xml_str = None
        for enc in ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1']:
            try:
                xml_str = original_content.decode(enc)
                break
            except Exception:
                continue

        if not xml_str:
            return original_content

        root = ET.fromstring(xml_str)
        ns = {'x': _XLIFF_NS, 'mq': _MQ_NS, 'sdl': _SDL_NS}

        trans_units = root.findall(".//x:trans-unit", ns)
        if not trans_units:
            trans_units = root.findall(".//trans-unit")

        for trans_unit in trans_units:
            seg_id = trans_unit.get('id')
            if seg_id not in translations:
                continue

            # Find or create <target>
            target = trans_unit.find("x:target", ns)
            if target is None:
                target = trans_unit.find("target")
            if target is None:
                target = ET.SubElement(trans_unit, f"{{{_XLIFF_NS}}}target")

            # Clear existing content
            target.text = None
            for child in list(target):
                target.remove(child)

            # Write translation with tag reconstruction
            trans_text = translations[seg_id]
            segment_obj = segments_map.get(seg_id)
            if segment_obj and segment_obj.tag_map:
                XMLParser._reconstruct_element(target, trans_text, segment_obj.tag_map)
            else:
                target.text = trans_text

            # Mark segment as Translated
            for attr_key in list(trans_unit.attrib.keys()):
                local = attr_key.split('}')[-1] if '}' in attr_key else attr_key
                if local == 'status':
                    trans_unit.attrib[attr_key] = "Translated"

        output_str = ET.tostring(root, encoding='unicode')

        # ── MemoQ namespace repair ────────────────────────────────────────
        if 'MQXliff' in output_str and 'xmlns:mq="MQXliff"' not in output_str:
            output_str = output_str.replace('<xliff ', '<xliff xmlns:mq="MQXliff" ', 1)

        mq_match = re.search(r'xmlns:(\w+)="MQXliff"', output_str)
        if mq_match:
            wrong = mq_match.group(1)
            if wrong != 'mq':
                output_str = output_str.replace(f'{wrong}:', 'mq:')
                output_str = output_str.replace(f'xmlns:{wrong}', 'xmlns:mq')

        # ── SDL namespace repair ──────────────────────────────────────────
        sdl_escaped = re.escape(_SDL_NS)
        if _SDL_NS in output_str and f'xmlns:sdl="{_SDL_NS}"' not in output_str:
            output_str = output_str.replace('<xliff ', f'<xliff xmlns:sdl="{_SDL_NS}" ', 1)

        sdl_match = re.search(r'xmlns:(\w+)="' + sdl_escaped + '"', output_str)
        if sdl_match:
            wrong = sdl_match.group(1)
            if wrong != 'sdl':
                output_str = output_str.replace(f'{wrong}:', 'sdl:')
                output_str = output_str.replace(f'xmlns:{wrong}', 'xmlns:sdl')

        final = f'<?xml version="1.0" encoding="UTF-8"?>\n{output_str}'
        return final.encode('utf-8')

    @staticmethod
    def parse_tmx(content: bytes) -> List[Dict]:
        """Parse a TMX translation memory file."""
        root = None
        for enc in ['utf-16', 'utf-8-sig', 'utf-8', 'latin-1']:
            try:
                xml_str = content.decode(enc)
                xml_str = re.sub(r'<\?xml.*encoding=["\'].*["\'].*\?>', '', xml_str, count=1)
                root = ET.fromstring(xml_str)
                break
            except Exception:
                continue

        if root is None:
            return []

        entries = []
        for tu in root.findall('.//tu'):
            tuvs = tu.findall('.//tuv')
            if len(tuvs) >= 2:
                entry: Dict[str, str] = {}
                for tuv in tuvs:
                    lang = tuv.get('{http://www.w3.org/XML/1998/namespace}lang')
                    seg = tuv.find('seg')
                    text = "".join(seg.itertext()) if seg is not None else ""
                    if lang:
                        # Store with BOTH the original key (lowered) and the normalized key
                        # This ensures matching works regardless of TMX source (memoQ or SDL)
                        original_key = lang.lower()
                        normalized_key = normalize_lang_code(lang)
                        entry[original_key] = text
                        if normalized_key != original_key:
                            entry[normalized_key] = text
                entries.append(entry)

        return entries
