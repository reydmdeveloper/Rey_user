import os
import re
import docx
import difflib


class MifTranslator:
    def __init__(self, docx_path):
        self.docx_path = docx_path
        self.en_to_it = {}
        self.load_docx()

    def load_docx(self):
        doc = docx.Document(self.docx_path)
        table = doc.tables[0]
        for idx, row in enumerate(table.rows[1:]):  # skip header
            cells = [c.text.strip() for c in row.cells]
            if len(cells) >= 3:
                en = cells[1].replace("\r\n", "\n").replace("\r", "")
                it = cells[2].replace("\r\n", "\n").replace("\r", "")
                clean_en = self.clean_text(en)
                if clean_en:
                    # Map clean English to original Italian and English with tags
                    self.en_to_it[clean_en] = {
                        'id': cells[0],
                        'en_raw': en,
                        'it_raw': it,
                        'row_idx': idx + 1
                    }

    def clean_text(self, text):
        # Remove formatting tags like {t1}, {/t1}, {f1/}, {a1/}
        text = re.sub(r"\{[^}]+\}", "", text)
        # Keep only letters and numbers for 100% robust matching across punctuation/symbol variations
        text = re.sub(r"[^a-zA-Z0-9]", "", text)
        return text.strip().lower()

    def parse_para_elements(self, para_block):
        lines = para_block.splitlines(keepends=True)
        elements = []
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Check String
            string_match = re.search(r"<String `([^']*)'>", line)
            if string_match:
                elements.append({
                    'type': 'string',
                    'text': string_match.group(1),
                    'line': line
                })
                i += 1
                continue
                
            # Check Font block
            if "<Font" in line:
                font_block = line
                i += 1
                while i < len(lines) and "> # end of Font" not in lines[i-1]:
                    font_block += lines[i]
                    i += 1
                elements.append({
                    'type': 'font',
                    'raw': font_block
                })
                continue
                
            # Check Variable block
            if "<Variable" in line and "<VariableName" in lines[min(i+1, len(lines)-1)]:
                var_block = line
                i += 1
                while i < len(lines) and "> # end of Variable" not in lines[i-1]:
                    var_block += lines[i]
                    i += 1
                elements.append({
                    'type': 'variable',
                    'raw': var_block
                })
                continue
                
            # Check Marker block
            if "<Marker" in line:
                marker_block = line
                i += 1
                while i < len(lines) and "> # end of Marker" not in lines[i-1]:
                    marker_block += lines[i]
                    i += 1
                elements.append({
                    'type': 'marker',
                    'raw': marker_block
                })
                continue
                
            # Check ATbl block
            atbl_match = re.search(r"<ATbl \d+>", line)
            if atbl_match:
                elements.append({
                    'type': 'atbl',
                    'raw': line
                })
                i += 1
                continue
                
            elements.append({
                'type': 'other',
                'raw': line
            })
            i += 1
        return elements

    def build_mif_from_translation(self, para_block, translation_data):
        elements = self.parse_para_elements(para_block)
        
        var_blocks = [el for el in elements if el['type'] == 'variable']
        font_blocks = [el for el in elements if el['type'] == 'font']
        anchor_blocks = [el for el in elements if el['type'] in ['atbl', 'marker']]
        
        # Extract TextRectID lines from the original para_block
        textrect_lines = []
        for line in para_block.splitlines():
            if "<TextRectID" in line:
                textrect_lines.append(line + "\n")
                
        it_raw = translation_data['it_raw'].replace("\r\n", "\n").replace("\r", "")
        
        # Tokenize the Italian translation text into tags and plain text
        tokens = re.split(r"(\{[^}]+\})", it_raw)
        
        first_paraline_idx = para_block.find("<ParaLine")
        if first_paraline_idx == -1:
            return para_block
            
        header = para_block[:first_paraline_idx]
        
        # Extract the closing line of the Para block as the footer
        para_lines = para_block.splitlines(keepends=True)
        while para_lines and not para_lines[-1].strip():
            para_lines.pop()
        if not para_lines:
            return para_block
        footer = para_lines[-1]
        
        body_lines = []
        body_lines.append("  <ParaLine\n")
        
        # Prepend TextRectID lines if any
        body_lines.extend(textrect_lines)
        
        seq_var_idx = 0
        seq_font_idx = 0
        seq_anchor_idx = 0
        
        consumed_vars = set()
        consumed_anchors = set()
        
        for token in tokens:
            if not token:
                continue
            
            if token.startswith("{") and token.endswith("}"):
                tag_content = token[1:-1]
                
                # Check Variable tags: {fX/}
                f_match = re.match(r"f(\d+)/", tag_content)
                if f_match:
                    idx = int(f_match.group(1)) - 1
                    if 0 <= idx < len(var_blocks):
                        body_lines.append(var_blocks[idx]['raw'])
                        consumed_vars.add(idx)
                    elif seq_var_idx < len(var_blocks):
                        body_lines.append(var_blocks[seq_var_idx]['raw'])
                        consumed_vars.add(seq_var_idx)
                        seq_var_idx += 1
                    continue
                    
                # Check Anchor tags: {aX/}
                a_match = re.match(r"a(\d+)/", tag_content)
                if a_match:
                    idx = int(a_match.group(1)) - 1
                    if 0 <= idx < len(anchor_blocks):
                        body_lines.append(anchor_blocks[idx]['raw'])
                        consumed_anchors.add(idx)
                    elif seq_anchor_idx < len(anchor_blocks):
                        body_lines.append(anchor_blocks[seq_anchor_idx]['raw'])
                        consumed_anchors.add(seq_anchor_idx)
                        seq_anchor_idx += 1
                    continue
                    
                # Check Font open tags: {tX}
                t_match = re.match(r"t(\d+)", tag_content)
                if t_match:
                    idx = int(t_match.group(1)) - 1
                    if 0 <= idx < len(font_blocks):
                        body_lines.append(font_blocks[idx]['raw'])
                    elif seq_font_idx < len(font_blocks):
                        body_lines.append(font_blocks[seq_font_idx]['raw'])
                        seq_font_idx += 1
                    continue
                    
                # Check Font close tags: {/tX}
                if tag_content.startswith("/t"):
                    reset_font = "   <Font \n    <FTag `'>\n    <FLocked No>\n   > # end of Font\n"
                    body_lines.append(reset_font)
                    continue
                    
                # Fallback for standard style tags: {[i]} / {/[i]} / {[b]} / {/[b]}
                if tag_content == "[i]":
                    body_lines.append("   <Font \n    <FTag `Italic'>\n    <FLocked No>\n   > # end of Font\n")
                elif tag_content in ["/[i]", "/i"]:
                    body_lines.append("   <Font \n    <FTag `'>\n    <FLocked No>\n   > # end of Font\n")
                elif tag_content == "[b]":
                    body_lines.append("   <Font \n    <FTag `Bold'>\n    <FLocked No>\n   > # end of Font\n")
                elif tag_content in ["/[b]", "/b"]:
                    body_lines.append("   <Font \n    <FTag `'>\n    <FLocked No>\n   > # end of Font\n")
            else:
                # Plain text token: split by newlines and tabs
                sub_tokens = re.split(r"([\n\t])", token)
                for sub_token in sub_tokens:
                    if not sub_token:
                        continue
                    if sub_token == "\n":
                        body_lines.append("   <Char HardReturn>\n")
                    elif sub_token == "\t":
                        body_lines.append("   <Char Tab>\n")
                    else:
                        text = sub_token
                        # Escape MIF special characters in correct order
                        text = text.replace("\\", "\\\\")
                        text = text.replace(">", "\\>")
                        text = text.replace("'", "\\q")
                        text = text.replace("`", "\\Q")
                        body_lines.append(f"   <String `{text}'>\n")
                        
        # Prepend unused anchors
        unused_anchors = []
        for idx, block in enumerate(anchor_blocks):
            if idx not in consumed_anchors:
                unused_anchors.append(block['raw'])
        body_lines.extend(unused_anchors)
        
        # Append unused variables
        for idx, block in enumerate(var_blocks):
            if idx not in consumed_vars:
                body_lines.append(block['raw'])
                
        body_lines.append("  > # end of ParaLine\n")
        
        return header + "".join(body_lines) + footer

    def find_para_end(self, content, start_pos):
        lines = content[start_pos:].splitlines()
        nesting = 0
        pos = start_pos
        for line in lines:
            stripped = line.strip()
            is_opener = False
            if stripped.startswith("<") and not stripped.endswith(">"):
                is_opener = True
            elif stripped.startswith("<Para") or stripped.startswith("<ParaLine") or stripped.startswith("<Font") or stripped.startswith("<Variable") or stripped.startswith("<Marker"):
                if not stripped.endswith(">"):
                    is_opener = True
                    
            is_closer = False
            if stripped == ">" or (stripped.startswith(">") and "# end of" in stripped):
                is_closer = True
                
            if is_opener:
                nesting += 1
            elif is_closer:
                nesting -= 1
                
            pos += len(line) + 1
            if nesting == 0:
                return pos
        return -1

    def unescape_mif_text(self, text):
        text = text.replace("\\Q", "`")
        text = text.replace("\\q", "'")
        text = text.replace("\\>", ">")
        text = text.replace("\\\\", "\\")
        return text

    def escape_mif_text(self, text):
        text = text.replace("\\", "\\\\")
        text = text.replace(">", "\\>")
        text = text.replace("'", "\\q")
        text = text.replace("`", "\\Q")
        return text

    def normalize_word(self, w):
        wl = w.lower().strip()
        if wl == "figure": return "figura"
        if wl == "figures": return "figure"
        if wl == "table": return "tabella"
        if wl == "tables": return "tabelle"
        if wl == "page": return "pagina"
        if wl == "on": return "a"
        if wl == "section": return "sezione"
        return w.strip()

    def map_char_index(self, x, E_text, T_text, E_toks, T_toks, matcher):
        matched_intervals = []
        for block in matcher.get_matching_blocks():
            if block.size > 0:
                e_start = E_toks[block.a][1]
                e_end = E_toks[block.a + block.size - 1][2]
                t_start = T_toks[block.b][1]
                t_end = T_toks[block.b + block.size - 1][2]
                matched_intervals.append((e_start, e_end, t_start, t_end))
                
        matched_intervals.insert(0, (0, 0, 0, 0))
        matched_intervals.append((len(E_text), len(E_text), len(T_text), len(T_text)))
        matched_intervals.sort(key=lambda item: item[0])
        
        for e_start, e_end, t_start, t_end in matched_intervals:
            if e_start <= x <= e_end:
                if e_end > e_start:
                    ratio = (x - e_start) / (e_end - e_start)
                    return int(t_start + ratio * (t_end - t_start))
                else:
                    return t_start
                    
        for k in range(len(matched_intervals) - 1):
            e_start_curr, e_end_curr, t_start_curr, t_end_curr = matched_intervals[k]
            e_start_next, e_end_next, t_start_next, t_end_next = matched_intervals[k+1]
            
            if e_end_curr <= x <= e_start_next:
                e_gap = e_start_next - e_end_curr
                t_gap = t_start_next - t_end_curr
                if e_gap > 0:
                    ratio = (x - e_end_curr) / e_gap
                    return int(t_end_curr + ratio * t_gap)
                else:
                    return t_end_curr
                    
        return len(T_text)

    def translate_mif(self, mif_content):
        # 1. Translate Variable formats in Catalog
        def replace_variable_def(match):
            block = match.group(0)
            name_match = re.search(r"<VariableName `([^']*)'>", block)
            def_match = re.search(r"<VariableDef `([^']*)'>", block)
            if name_match and def_match:
                var_name = name_match.group(1)
                var_def = def_match.group(1)
                clean_def = self.clean_text(var_def)
                if clean_def in self.en_to_it:
                    it_val = self.en_to_it[clean_def]['it_raw']
                    it_val = re.sub(r"\{[^}]+\}", "", it_val)  # remove tags
                    it_val = it_val.replace("\r\n", "\n").replace("\r", "")
                    # Escape delimiters in correct order
                    it_val = self.escape_mif_text(it_val)
                    print(f"Translating Variable Definition '{var_name}': '{var_def}' -> '{it_val}'")
                    new_block = block.replace(f"<VariableDef `{var_def}'>", f"<VariableDef `{it_val}'>")
                    return new_block
            return block

        mif_content = re.sub(r"<VariableFormat\b.*?> # end of VariableFormat", replace_variable_def, mif_content, flags=re.DOTALL)

        # 2. Translate Paragraphs
        para_matches = list(re.finditer(r"<Para\b", mif_content))
        
        new_content_parts = []
        last_pos = 0
        
        for match in para_matches:
            para_start = match.start()
            if para_start < last_pos:
                continue
                
            para_end = self.find_para_end(mif_content, para_start)
            if para_end == -1:
                continue
            
            new_content_parts.append(mif_content[last_pos:para_start])
            
            para_block = mif_content[para_start:para_end]
            
            # Find all String and Char elements in the paragraph block
            lines = para_block.splitlines(keepends=True)
            text_elements = []
            for idx, line in enumerate(lines):
                str_match = re.search(r"<String `([^']*)'>", line)
                if str_match:
                    text_elements.append({
                        'line_idx': idx,
                        'type': 'string',
                        'text': str_match.group(1)
                    })
                char_match = re.search(r"<Char ([A-Za-z0-9]+)>", line)
                if char_match:
                    text_elements.append({
                        'line_idx': idx,
                        'type': 'char',
                        'char_type': char_match.group(1)
                    })
            
            # Reconstruct the plain English text of the paragraph
            E_text_parts = []
            string_ranges = []
            current_len = 0
            for idx, el in enumerate(text_elements):
                if el['type'] == 'string':
                    val = self.unescape_mif_text(el['text'])
                    E_text_parts.append(val)
                    string_ranges.append((idx, current_len, current_len + len(val)))
                    current_len += len(val)
                elif el['type'] == 'char':
                    char_type = el['char_type']
                    if char_type == 'HardSpace':
                        val = " "
                    elif char_type in ['HardHyphen', 'SoftHyphen']:
                        val = "-"
                    elif char_type == 'Tab':
                        val = "\t"
                    elif char_type == 'HardReturn':
                        val = "\n"
                    else:
                        val = ""
                    E_text_parts.append(val)
                    current_len += len(val)
            
            E_text = "".join(E_text_parts)
            p_clean = self.clean_text(E_text)
            
            translated_para = para_block
            if p_clean:
                T_text = None
                
                # 2.1 Direct exact match
                if p_clean in self.en_to_it:
                    it_raw = self.en_to_it[p_clean]['it_raw']
                    T_text = re.sub(r"\{[^}]+\}", "", it_raw).replace("\r\n", "\n").replace("\r", "")
                else:
                    # 2.2 Split sentence mapping
                    matches = []
                    for entry_clean, data in self.en_to_it.items():
                        for m in re.finditer(re.escape(entry_clean), p_clean):
                            matches.append((m.start(), m.end(), entry_clean, data))
                    
                    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
                    
                    selected_matches = []
                    last_end = 0
                    for start, end, entry_clean, data in matches:
                        if start >= last_end:
                            selected_matches.append((start, end, entry_clean, data))
                            last_end = end
                            
                    total_matched_len = sum(end - start for start, end, _, _ in selected_matches)
                    if len(p_clean) > 0 and total_matched_len >= 0.75 * len(p_clean):
                        selected_matches.sort(key=lambda x: x[0])
                        combined_it_raw = " ".join([m[3]['it_raw'] for m in selected_matches])
                        T_text = re.sub(r"\{[^}]+\}", "", combined_it_raw).replace("\r\n", "\n").replace("\r", "")
                
                if T_text is not None:
                    # Align E_text and T_text using difflib
                    E_toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\w+|[^\w\s]|\s+", E_text)]
                    T_toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\w+|[^\w\s]|\s+", T_text)]
                    
                    E_vals = [self.normalize_word(tok[0]).lower() for tok in E_toks]
                    T_vals = [self.normalize_word(tok[0]).lower() for tok in T_toks]
                    
                    matcher = difflib.SequenceMatcher(None, E_vals, T_vals)
                    
                    new_lines = list(lines)
                    for str_idx, start_char, end_char in string_ranges:
                        el = text_elements[str_idx]
                        start_T = self.map_char_index(start_char, E_text, T_text, E_toks, T_toks, matcher)
                        end_T = self.map_char_index(end_char, E_text, T_text, E_toks, T_toks, matcher)
                        new_val = T_text[start_T:end_T]
                        new_val_escaped = self.escape_mif_text(new_val)
                        
                        orig_line = lines[el['line_idx']]
                        start_idx = orig_line.find("<String `")
                        if start_idx != -1:
                            end_idx = orig_line.find("'>", start_idx)
                            if end_idx != -1:
                                new_line = orig_line[:start_idx + 9] + new_val_escaped + orig_line[end_idx:]
                                new_lines[el['line_idx']] = new_line

                    
                    translated_para = "".join(new_lines)
            
            new_content_parts.append(translated_para)
            last_pos = para_end
            
        new_content_parts.append(mif_content[last_pos:])
        return "".join(new_content_parts)

    def translate_file(self, input_mif_path, output_mif_path):
        with open(input_mif_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            
        translated_content = self.translate_mif(content)
        
        with open(output_mif_path, "w", encoding="utf-8") as f:
            f.write(translated_content)
        print(f"MIF file translated successfully and saved to: {output_mif_path}")


if __name__ == "__main__":
    src_mif = r"d:\Dharani Projects\Project\REYDM\FM Copy&Paste 2\Using this Manual.mif"
    dest_mif = r"d:\Dharani Projects\Project\REYDM\FM Copy&Paste 2\Using this Manual_translated.mif"
    docx_file = r"d:\Dharani Projects\Project\REYDM\FM Copy&Paste 2\using this manual_en_it-it.docx"
    
    if os.path.exists(src_mif) and os.path.exists(docx_file):
        translator = MifTranslator(docx_file)
        translator.translate_file(src_mif, dest_mif)
