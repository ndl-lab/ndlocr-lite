import flet as ft
from pathlib import Path


class TableStructureFeature:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            import tablecenternet

            self._engine = tablecenternet
        return self._engine

    def default_config(self):
        return {'table_structure': True}

    def add_arguments(self, parser, source_dir):
        parser.add_argument(
            '--table-weights',
            type=str,
            required=False,
            help='Path to TableCenterNet-S ONNX file',
            default=str(source_dir / 'model' / 'tablecenternet_s_800_v3.onnx'),
        )
        parser.add_argument(
            '--table-decoder',
            type=str,
            choices=['simple', 'official'],
            default='official',
        )
        parser.add_argument('--table-center-thresh', type=float, default=0.2)
        parser.add_argument('--table-corner-thresh', type=float, default=0.3)
        parser.add_argument(
            '--table-crop-padding',
            type=int,
            default=8,
            help=(
                'Minimum pixel margin around layout-detected table crops '
                '(an additional 2% proportional margin is applied)'
            ),
        )

    def create_recognizer(self, args):
        return self.engine.TableCenterNetRecognizer(
            model_path=args.table_weights,
            device=args.device,
            decoder=args.table_decoder,
            center_thresh=args.table_center_thresh,
            corner_thresh=args.table_corner_thresh,
        )

    def infer(self, image, page_result, recognizer, crop_padding=None):
        kwargs = {}
        if crop_padding is not None:
            kwargs['crop_padding'] = max(0, int(crop_padding))
        return self.engine.infer_tables_from_page(
            image,
            page_result,
            recognizer,
            **kwargs,
        )

    def markdown_fragments(self, page_tables):
        fragments = []
        for item in list(page_tables or []):
            if item.get('error'):
                message = str(item.get('error')).replace('|', '\\|')
                markdown = f'_表構造の推定に失敗しました: {message}_'
            else:
                markdown = self.engine.cells_to_markdown(list(item.get('cells', [])))
            fragments.append({
                'markdown': markdown,
                'layout_bbox': item.get('layout_bbox'),
                'table_index': item.get('table_index'),
                'source_table_indexes': item.get('source_table_indexes'),
            })
        return fragments

    def write_html_files(self, document_name, page_tables, output_dir, output_stem):
        paths = []
        for item in page_tables:
            item.pop('html_path', None)
            rows, cols = self.engine.table_grid_shape(item.get('cells', []))
            if item.get('error') or (rows < 2 and cols < 2):
                continue
            table_index = int(item.get('table_index', 1))
            page_index = item.get('page_index')
            page_suffix = '' if page_index is None else f'_page_{int(page_index) + 1:05}'
            path = Path(output_dir) / f'{output_stem}{page_suffix}_table_{table_index:05}.html'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.build_html(document_name, [item]), encoding='utf-8')
            item['html_path'] = str(path.resolve())
            paths.append(path)
        return paths

    def preview_fragments(self, page_tables):
        fragments = []
        for item in page_tables or []:
            path = item.get('html_path')
            if path:
                markdown = f'[表 {item.get("table_index", 1)} をHTMLで開く]({Path(path).as_uri()})'
            elif item.get('error'):
                message = str(item['error'])
                for char in ('\\', '*', '_', '[', ']', '<', '>', '|'):
                    message = message.replace(char, '\\' + char)
                markdown = f'_表構造の推定に失敗しました: {message}_'
            else:
                markdown = ''
            fragments.append({
                'markdown': markdown,
                'layout_bbox': item.get('layout_bbox'),
                'table_index': item.get('table_index'),
                'source_table_indexes': item.get('source_table_indexes'),
            })
        return fragments

    def build_html(self, document_name, table_results):
        return self.engine.build_tables_html_document(document_name, table_results)

    def create_output_control(self, config_obj):
        return ft.Checkbox(
            label='表構造の推定（HTML出力）',
            value=config_obj.get('table_structure', True),
            key='output_table_structure',
        )

    def create_capture_control(self, config_obj):
        return ft.Checkbox(
            label='表の構造化',
            value=config_obj.get('table_structure', True),
            key='capture_table_structure',
        )


def create_feature():
    return TableStructureFeature()
