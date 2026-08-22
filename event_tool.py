#!/usr/bin/env python3
"""
event_tool.py - Extraction and Repacking Tool for event.dat game scripts using kurayami.tbl
"""

import sys, os, re, json, struct, argparse
from typing import List, Dict, Any, Tuple

DIALOG_OPCODE = b'\x03\x01\x00\x00'

class TBLCodec:
    def __init__(self, tbl_path: str = None):
        self.decode_map = {}
        self.encode_map = {}
        if tbl_path and os.path.exists(tbl_path):
            self.load_tbl(tbl_path)
            
    def load_tbl(self, tbl_path: str):
        with open(tbl_path, 'r', encoding='shift-jis', errors='replace') as f:
            for line in f:
                line = line.rstrip('r\n')
                if '=' in line:
                    hex_key, char_val = line.split('=', 1)
                    try:
                        byte_key = bytes.fromhex(hex_key)
                        self.decode_map[byte_key] = char_val
                        self.encode_map[char_val] = byte_key
                    except ValueError:
                        continue

    def decode(self, data: bytes) -> str:
        if not self.decode_map:
            return data.decode('cp932', errors='replace')
            
        res = []
        i = 0
        while i < len(data):
            if i + 2 <= len(data) and data[i:i+2] in self.decode_map:
                res.append(self.decode_map[data[i:i+2]])
                i += 2
            elif data[i:i+1] in self.decode_map:
                res.append(self.decode_map[data[i:i+1]])
                i += 1
            else:
                b = data[i]
                res.append(f'<{b:02X}>')
                i += 1
        return ''.join(res)

    def encode(self, text: str) -> bytes:
        if not self.encode_map:
            return text.encode('cp932', errors='replace')
            
        # ASCII to game font (full-width) mapping table
        ascii_to_game_font = {
            **{chr(ord('A') + k): chr(ord('Ａ') + k) for k in range(26)},
            **{chr(ord('a') + k): chr(ord('ａ') + k) for k in range(26)},
            **{chr(ord('0') + k): chr(ord('０') + k) for k in range(10)},
            ' ': '　',
            '!': '！',
            '?': '？',
            ',': '，',
            '.': '．',
            '-': 'ー',
            ':': '：',
            ';': '；',
        }
            
        res = bytearray()
        i = 0
        while i < len(text):
            if text[i] == '<' and '>' in text[i+1:i+7]:
                end_idx = text.find('>', i+1)
                inner = text[i+1:end_idx]
                if len(inner) in (2, 4):
                    try:
                        b_val = bytes.fromhex(inner)
                        res.extend(b_val)
                        i = end_idx + 1
                        continue
                    except ValueError:
                        pass
            
            ch = text[i]
            # Convert standard ASCII to game font character if available
            target_ch = ascii_to_game_font.get(ch, ch)
            
            if target_ch in self.encode_map:
                res.extend(self.encode_map[target_ch])
            elif ch in self.encode_map:
                res.extend(self.encode_map[ch])
            else:
                try:
                    res.extend(ch.encode('cp932'))
                except UnicodeEncodeError:
                    res.append(0x3F)
            i += 1
        return bytes(res)

def read_root_offsets(data: bytes) -> List[int]:
    offsets = []
    for i in range(0, 0x80, 4):
        off = struct.unpack('<I', data[i:i+4])[0]
        if off != 0:
            offsets.append(off)
    return offsets

def parse_subfile_structure(data: bytes, sub_off: int) -> Tuple[List[int], List[int]]:
    sub_data = data[sub_off:]
    block_ptrs = []
    for i in range(0, 0x40, 4):
        val = struct.unpack('<I', sub_data[i:i+4])[0]
        if val != 0:
            block_ptrs.append(val)
            
    if not block_ptrs:
        return [], []

    b0_rel = block_ptrs[0]
    b1_rel = block_ptrs[1] if len(block_ptrs) > 1 else len(sub_data)
    
    scene_ptrs = []
    for i in range(b0_rel, min(b0_rel + 0x200, b1_rel), 4):
        val = struct.unpack('<I', sub_data[i:i+4])[0]
        if val == 0 or val >= b1_rel:
            break
        scene_ptrs.append(val)
        
    return block_ptrs, scene_ptrs

def extract_dialogs_from_bytes(chunk: bytes, base_file_offset: int, codec: TBLCodec) -> List[Dict[str, Any]]:
    dialogs = []
    pos = 0
    while pos < len(chunk) - 6:
        if chunk[pos:pos+4] == DIALOG_OPCODE and chunk[pos+6] == 0x07:
            start_pos = pos
            msg_id = struct.unpack('<H', chunk[pos+4:pos+6])[0]
            p = pos + 7
            
            nametag_bytes = b''
            has_nametag = False
            if p < len(chunk) and chunk[p] == 0x1C:
                has_nametag = True
                p += 1
                name_start = p
                while p < len(chunk) and chunk[p] != 0x11:
                    p += 1
                nametag_bytes = chunk[name_start:p]
                if p < len(chunk) and chunk[p] == 0x11:
                    p += 1
            
            text_start = p
            while p < len(chunk) and chunk[p] not in (0x00, 0x12):
                p += 1
            
            text_bytes = chunk[text_start:p]
            term_byte = chunk[p] if p < len(chunk) else 0x12
            p += 1
            
            voice_id = ''
            if text_bytes.startswith(b'\x1e'):
                v_end = 1
                while v_end < len(text_bytes) and chr(text_bytes[v_end]).isdigit():
                    v_end += 1
                voice_id = text_bytes[1:v_end].decode('ascii', errors='ignore')
                text_bytes = text_bytes[v_end:]
            
            raw_lines = text_bytes.split(b'\x11')
            decoded_lines = [codec.decode(l) for l in raw_lines]
            text_str = '\n'.join(decoded_lines)
            nametag_str = codec.decode(nametag_bytes)
            
            dialogs.append({
                'dialog_index': len(dialogs),
                'scene_rel_offset': hex(start_pos),
                'file_offset': hex(base_file_offset + start_pos),
                'msg_id': msg_id,
                'has_nametag': has_nametag,
                'nametag': nametag_str,
                'voice_id': voice_id,
                'text': text_str,
                '_term_byte': term_byte,
                '_orig_start': start_pos,
                '_orig_end': p
            })
            pos = p
        else:
            pos += 1
    return dialogs

def extract_data(dat_path: str, codec: TBLCodec, subfile_idx: int = None, scene_idx: int = None) -> Dict[str, Any]:
    with open(dat_path, 'rb') as f:
        data = f.read()

    root_offsets = read_root_offsets(data)
    result = {
        'source_file': os.path.basename(dat_path),
        'total_subfiles': len(root_offsets),
        'subfiles': []
    }

    subfiles_to_process = range(len(root_offsets)) if subfile_idx is None else [subfile_idx]

    for s_idx in subfiles_to_process:
        s_off = root_offsets[s_idx]
        block_ptrs, scene_ptrs = parse_subfile_structure(data, s_off)
        if not scene_ptrs:
            continue
        
        b1_rel = block_ptrs[1] if len(block_ptrs) > 1 else len(data) - s_off
        subfile_entry = {
            'subfile_index': s_idx,
            'subfile_offset': hex(s_off),
            'total_scenes': len(scene_ptrs),
            'scenes': []
        }

        scenes_to_process = range(len(scene_ptrs)) if scene_idx is None else [scene_idx]

        for sc_idx in scenes_to_process:
            sc_start = scene_ptrs[sc_idx]
            sc_end = scene_ptrs[sc_idx + 1] if sc_idx + 1 < len(scene_ptrs) else b1_rel
            
            sc_abs_start = s_off + sc_start
            sc_abs_end = s_off + sc_end
            chunk = data[sc_abs_start:sc_abs_end]
            
            dialogs = extract_dialogs_from_bytes(chunk, sc_abs_start, codec)
            subfile_entry['scenes'].append({
                'scene_index': sc_idx,
                'scene_rel_offset': hex(sc_start),
                'scene_file_offset': hex(sc_abs_start),
                'scene_size': len(chunk),
                'dialog_count': len(dialogs),
                'dialogs': dialogs
            })

        result['subfiles'].append(subfile_entry)

    return result

def repack_scene(original_chunk: bytes, dialogs: List[Dict[str, Any]], codec: TBLCodec) -> bytes:
    out = bytearray()
    last_end = 0

    for d in dialogs:
        start = d.get('_orig_start')
        end = d.get('_orig_end')
        
        # If not present in JSON (e.g. hand edited), find next DIALOG_OPCODE
        if start is None or end is None:
            continue
            
        out.extend(original_chunk[last_end:start])
        
        msg_id = d.get('msg_id', struct.unpack('<H', original_chunk[start+4:start+6])[0])
        out.extend(DIALOG_OPCODE)
        out.extend(struct.pack('<H', msg_id))
        out.append(0x07)
        
        if d.get('has_nametag', False):
            out.append(0x1C)
            name_bytes = codec.encode(d.get('nametag', ''))
            out.extend(name_bytes)
            out.append(0x11)
            
        voice_id = d.get('voice_id', '')
        if voice_id:
            out.append(0x1E)
            out.extend(voice_id.encode('ascii'))
            
        text_str = d.get('text', '')
        lines = text_str.split('\n')
        encoded_lines = [codec.encode(l) for l in lines]
        text_bytes = b'\x11'.join(encoded_lines)
        out.extend(text_bytes)
        
        term_byte = d.get('_term_byte', 0x12)
        out.append(term_byte)
        
        last_end = end

    out.extend(original_chunk[last_end:])
    return bytes(out)

def relocate_subfile_bytecode(data: bytearray, old_block_ptrs: list, new_block_ptrs: list):
    for i in range(len(data) - 5):
        if data[i] == 0x01:
            val = struct.unpack('<I', data[i+1:i+5])[0]
            for b_idx in range(1, len(old_block_ptrs)):
                b_start = old_block_ptrs[b_idx]
                b_end = old_block_ptrs[b_idx+1] if b_idx+1 < len(old_block_ptrs) else len(data)
                if b_start <= val < b_end:
                    delta = new_block_ptrs[b_idx] - b_start
                    if delta != 0:
                        new_val = val + delta
                        data[i+1:i+5] = struct.pack('<I', new_val)
                    break

def repack_dat(original_dat_path: str, json_path: str, codec: TBLCodec, output_dat_path: str, preserve_filesize: bool = True):
    with open(original_dat_path, 'rb') as f:
        orig_data = f.read()

    with open(json_path, 'r', encoding='utf-8') as f:
        mod_json = json.load(f)

    mod_map = {}
    for sub in mod_json.get('subfiles', []):
        s_idx = sub['subfile_index']
        for sc in sub.get('scenes', []):
            sc_idx = sc['scene_index']
            mod_map[(s_idx, sc_idx)] = sc['dialogs']

    root_offsets = read_root_offsets(orig_data)
    new_subfiles = []

    for s_idx, s_off in enumerate(root_offsets):
        s_end = root_offsets[s_idx + 1] if s_idx + 1 < len(root_offsets) else len(orig_data)
        sub_data = orig_data[s_off:s_end]
        
        # If this is the trailing empty padding subfile (subfile 28)
        if s_idx == len(root_offsets) - 1 and len(sub_data) > 0 and all(b == 0 for b in sub_data[:1024]):
            new_subfiles.append(b'')
            continue

        # Check if any scene in this subfile is modified
        has_mods = any(s == s_idx for (s, _) in mod_map.keys())
        if not has_mods:
            new_subfiles.append(sub_data)
            continue

        block_ptrs, scene_ptrs = parse_subfile_structure(orig_data, s_off)
        if not scene_ptrs:
            new_subfiles.append(sub_data)
            continue
            
        b0_rel = block_ptrs[0]
        b1_rel = block_ptrs[1] if len(block_ptrs) > 1 else len(sub_data)
        
        new_scenes = []
        for sc_idx, sc_start in enumerate(scene_ptrs):
            sc_end = scene_ptrs[sc_idx + 1] if sc_idx + 1 < len(scene_ptrs) else b1_rel
            orig_scene_chunk = sub_data[sc_start:sc_end]
            
            if (s_idx, sc_idx) in mod_map:
                repacked_scene = repack_scene(orig_scene_chunk, mod_map[(s_idx, sc_idx)], codec)
                new_scenes.append(repacked_scene)
            else:
                new_scenes.append(orig_scene_chunk)
        
        new_scene_ptrs = []
        cur_scene_offset = scene_ptrs[0]
        for sc in new_scenes:
            new_scene_ptrs.append(cur_scene_offset)
            cur_scene_offset += len(sc)
            
        new_b0_header = bytearray(sub_data[b0_rel:b0_rel + len(scene_ptrs)*4])
        for sc_idx, new_ptr in enumerate(new_scene_ptrs):
            new_b0_header[sc_idx*4:(sc_idx+1)*4] = struct.pack('<I', new_ptr)
            
        b0_pad = sub_data[b0_rel + len(scene_ptrs)*4 : scene_ptrs[0]]
        new_b0 = bytes(new_b0_header) + b0_pad + b''.join(new_scenes)
        
        orig_b0_len = b1_rel - b0_rel
        b0_delta = len(new_b0) - orig_b0_len
        
        new_block_ptrs = list(block_ptrs)
        new_sub_header = bytearray(sub_data[:b0_rel])
        for b_idx in range(1, len(block_ptrs)):
            new_block_ptrs[b_idx] = block_ptrs[b_idx] + b0_delta
            new_sub_header[b_idx*4:(b_idx+1)*4] = struct.pack('<I', new_block_ptrs[b_idx])
            
        trailing_blocks = sub_data[b1_rel:]
        raw_subfile = bytearray(bytes(new_sub_header) + new_b0 + trailing_blocks)
        
        if b0_delta != 0:
            relocate_subfile_bytecode(raw_subfile, block_ptrs, new_block_ptrs)
            
        new_subfiles.append(bytes(raw_subfile))

    # Compute root offsets
    new_root_offsets = []
    cur_root_off = root_offsets[0]
    for i in range(len(new_subfiles) - 1):
        new_root_offsets.append(cur_root_off)
        cur_root_off += len(new_subfiles[i])

    # Size trailing padding subfile to match original file size
    if preserve_filesize and cur_root_off < len(orig_data):
        pad_size = len(orig_data) - cur_root_off
        new_subfiles[-1] = bytes(pad_size)
    else:
        new_subfiles[-1] = b''

    new_root_offsets.append(cur_root_off)

    root_header = bytearray(orig_data[:root_offsets[0]])
    for i, off in enumerate(new_root_offsets):
        root_header[i*4:(i+1)*4] = struct.pack('<I', off)

    final_data = bytes(root_header) + b''.join(new_subfiles)
    
    with open(output_dat_path, 'wb') as f:
        f.write(final_data)
        
    print(f'Repacking complete: "{output_dat_path}" ({len(final_data)} bytes written, matches original size: {len(final_data) == len(orig_data)}).')

def main():
    parser = argparse.ArgumentParser(description='Extract and Repack event.dat game script using kurayami.tbl')
    parser.add_argument('--tbl', default='kurayami.tbl', help='Path to .tbl mapping file (default: kurayami.tbl)')
    subparsers = parser.add_subparsers(dest='cmd', required=True)

    p_ext = subparsers.add_parser('extract', help='Extract scenes to JSON')
    p_ext.add_argument('--tbl', default='kurayami.tbl', help='Path to .tbl mapping file')
    p_ext.add_argument('dat_file', help='Path to event.dat')
    p_ext.add_argument('-o', '--output', default='extracted_events.json', help='Output JSON file')
    p_ext.add_argument('--subfile', type=int, default=None, help='Specific subfile index (0-28)')
    p_ext.add_argument('--scene', type=int, default=None, help='Specific scene index within subfile')

    p_rep = subparsers.add_parser('repack', help='Repack modified JSON back to event.dat')
    p_rep.add_argument('--tbl', default='kurayami.tbl', help='Path to .tbl mapping file')
    p_rep.add_argument('dat_file', help='Original event.dat')
    p_rep.add_argument('json_file', help='Modified JSON file')
    p_rep.add_argument('-o', '--output', default='event_repacked.dat', help='Output .dat file')

    args = parser.parse_args()
    codec = TBLCodec(args.tbl if os.path.exists(args.tbl) else None)

    if args.cmd == 'extract':
        data = extract_data(args.dat_file, codec, args.subfile, args.scene)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'Extracted {len(data["subfiles"])} subfile(s) to "{args.output}".')
    elif args.cmd == 'repack':
        repack_dat(args.dat_file, args.json_file, codec, args.output)

if __name__ == '__main__':
    main()
