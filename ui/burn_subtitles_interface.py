"""Desktop interface for burning SRT/ASS subtitles into a video."""

import os
from pathlib import Path
import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CaptionLabel,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    InfoBar,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    TitleLabel,
)

from backend.config import tr
from backend.tools.subtitle_burner import BurnCancelled, BurnOptions, SubtitleBurner


def burn_text(key, fallback):
    if tr.has_section('BurnSubtitles'):
        return tr['BurnSubtitles'].get(key, fallback)
    return fallback


class BurnSubtitlesInterface(QWidget):
    progress_signal = Signal(int)
    log_signal = Signal(str)
    completed_signal = Signal(object)
    failed_signal = Signal(str)
    cancelled_signal = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._thread = None
        self._completed_path = None
        self._build_ui()
        self.progress_signal.connect(self._on_progress)
        self.log_signal.connect(self._append_log)
        self.completed_signal.connect(self._on_completed)
        self.failed_signal.connect(self._on_failed)
        self.cancelled_signal.connect(self._on_cancelled)

    def _path_row(self, button_text, save=False, filters='All Files (*)'):
        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = LineEdit(widget)
        button = PushButton(button_text, widget)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        if save:
            button.clicked.connect(lambda: self._choose_output(edit, filters))
        else:
            button.clicked.connect(lambda: self._choose_input(edit, filters))
        return widget, edit

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        content = QWidget(scroll)
        content.setObjectName('burnContent')
        content.setStyleSheet('#burnContent { background: transparent; }')
        page = QVBoxLayout(content)
        page.setContentsMargins(24, 18, 24, 18)
        page.setSpacing(14)
        page.addWidget(TitleLabel(burn_text('Title', 'Burn Subtitles'), content))
        intro = BodyLabel(burn_text(
            'Intro', 'Add SRT or ASS subtitles permanently to a video.'
        ), content)
        intro.setWordWrap(True)
        page.addWidget(intro)

        files_card = CardWidget(self)
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(18, 16, 18, 16)
        files_layout.setSpacing(10)
        files_layout.addWidget(StrongBodyLabel(burn_text('FilesSection', 'Files'), files_card))
        files_form = QFormLayout()
        files_form.setContentsMargins(0, 0, 0, 0)
        files_form.setHorizontalSpacing(16)
        files_form.setVerticalSpacing(10)
        self.video_row, self.video_edit = self._path_row(
            burn_text('Browse', 'Browse'),
            filters=(
                'Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v '
                '*.ts *.m2ts *.mts);;Transport Stream (*.ts *.m2ts *.mts);;'
                'All Files (*)'
            ),
        )
        self.subtitle_row, self.subtitle_edit = self._path_row(
            burn_text('Browse', 'Browse'),
            filters='Subtitle Files (*.srt *.ass);;SRT (*.srt);;ASS (*.ass)',
        )
        self.output_row, self.output_edit = self._path_row(
            burn_text('Browse', 'Browse'), save=True,
            filters='MP4 Video (*.mp4);;Matroska Video (*.mkv);;MOV Video (*.mov);;All Files (*)',
        )
        self.video_edit.editingFinished.connect(self._suggest_output)
        self.subtitle_edit.textChanged.connect(self._update_style_state)
        self._auto_output_path = None
        self.output_edit.textEdited.connect(self._clear_auto_output)
        files_form.addRow(BodyLabel(burn_text('InputVideo', 'Input video')), self.video_row)
        files_form.addRow(BodyLabel(burn_text('SubtitleFile', 'Subtitle (SRT/ASS)')), self.subtitle_row)
        files_form.addRow(BodyLabel(burn_text('OutputVideo', 'Output video')), self.output_row)
        files_layout.addLayout(files_form)
        page.addWidget(files_card)

        options_card = CardWidget(self)
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(18, 16, 18, 16)
        options_layout.setSpacing(12)
        options_layout.addWidget(StrongBodyLabel(
            burn_text('StyleSection', 'Subtitle style (SRT)'), options_card
        ))
        options_grid = QGridLayout()
        options_grid.setHorizontalSpacing(16)
        options_grid.setVerticalSpacing(10)
        options_grid.setColumnStretch(0, 1)
        options_grid.setColumnStretch(1, 1)
        self.font_edit = LineEdit(options_card)
        self.font_edit.setText('Microsoft YaHei')
        self.font_size = SpinBox(options_card)
        self.font_size.setRange(8, 200)
        self.font_size.setValue(48)
        self.margin_bottom = SpinBox(options_card)
        self.margin_bottom.setRange(0, 1000)
        self.margin_bottom.setValue(40)
        self.outline = DoubleSpinBox(options_card)
        self.outline.setRange(0, 20)
        self.outline.setSingleStep(0.5)
        self.outline.setValue(2)
        self.shadow = DoubleSpinBox(options_card)
        self.shadow.setRange(0, 20)
        self.shadow.setSingleStep(0.5)
        self.shadow.setValue(1)
        self.color_edit = LineEdit(options_card)
        self.color_edit.setText('#FFFFFF')
        self.color_button = PushButton(burn_text('ChooseColor', 'Choose color'), options_card)
        color_row = QWidget(options_card)
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.addWidget(self.color_edit, 1)
        color_layout.addWidget(self.color_button)
        self.color_button.clicked.connect(self._choose_color)
        self._add_field(options_grid, 0, 0, burn_text('Font', 'Font'), self.font_edit)
        self._add_field(options_grid, 0, 1, burn_text('FontSize', 'Font size'), self.font_size)
        self._add_field(options_grid, 1, 0, burn_text('BottomMargin', 'Bottom margin'), self.margin_bottom)
        self._add_field(options_grid, 1, 1, burn_text('TextColor', 'Text color'), color_row)
        self._add_field(options_grid, 2, 0, burn_text('Outline', 'Outline'), self.outline)
        self._add_field(options_grid, 2, 1, burn_text('Shadow', 'Shadow'), self.shadow)
        options_layout.addLayout(options_grid)
        self.ass_note = CaptionLabel(burn_text(
            'AssStyleNote', 'ASS input keeps its embedded styles; SRT uses these settings.'
        ), options_card)
        self.ass_note.setWordWrap(True)
        options_layout.addWidget(self.ass_note)

        encode_card = CardWidget(self)
        encode_layout = QVBoxLayout(encode_card)
        encode_layout.setContentsMargins(18, 16, 18, 16)
        encode_layout.setSpacing(12)
        encode_layout.addWidget(StrongBodyLabel(
            burn_text('EncodeSection', 'Video encoding'), encode_card
        ))
        self.encoder = ComboBox(encode_card)
        self.encoder.addItem(
            burn_text('EncoderAuto', 'Auto (prefer NVIDIA)'), userData='auto'
        )
        self.encoder.addItem('CPU H.264 (libx264)', userData='libx264')
        self.encoder.addItem('CPU H.265 (libx265)', userData='libx265')
        self.encoder.addItem(
            'NVIDIA H.264 (h264_nvenc)', userData='h264_nvenc'
        )
        self.encoder.addItem(
            'NVIDIA H.265 (hevc_nvenc)', userData='hevc_nvenc'
        )
        self.bitrate_mode = ComboBox(encode_card)
        self.bitrate_mode.addItem(
            burn_text('MatchSource', 'Approximate source size (recommended)'),
            userData='source',
        )
        self.bitrate_mode.addItem(
            burn_text('QualityMode', 'Quality first (unrestricted size)'),
            userData='quality',
        )
        self.bitrate_mode.currentIndexChanged.connect(
            self._update_bitrate_mode_state
        )
        self.quality = SpinBox(encode_card)
        self.quality.setRange(0, 51)
        self.quality.setValue(20)
        self.quality.setToolTip(burn_text(
            'QualityHint', 'Lower values improve quality and increase file size.'
        ))
        encode_grid = QGridLayout()
        encode_grid.setHorizontalSpacing(16)
        encode_grid.setVerticalSpacing(10)
        encode_grid.setColumnStretch(0, 1)
        encode_grid.setColumnStretch(1, 1)
        self._add_field(encode_grid, 0, 0, burn_text('Encoder', 'Video encoder'), self.encoder)
        self._add_field(encode_grid, 1, 0, burn_text('BitrateMode', 'Output size'), self.bitrate_mode)
        self._add_field(encode_grid, 2, 0, burn_text('Quality', 'Quality (CRF/CQ)'), self.quality)
        encode_layout.addLayout(encode_grid)
        self.size_note = CaptionLabel(burn_text(
            'SourceSizeHint',
            'Source-size mode matches the original video bitrate. Exact size may vary.',
        ), encode_card)
        self.size_note.setWordWrap(True)
        encode_layout.addWidget(self.size_note)
        settings_row = QHBoxLayout()
        settings_row.setSpacing(14)
        settings_row.addWidget(options_card, 1)
        settings_row.addWidget(encode_card, 1)
        page.addLayout(settings_row)
        self._update_bitrate_mode_state()

        log_card = CardWidget(self)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(18, 14, 18, 16)
        log_layout.setSpacing(8)
        log_layout.addWidget(StrongBodyLabel(burn_text('LogSection', 'Details'), log_card))
        self.log_output = PlainTextEdit(log_card)
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(112)
        self.log_output.setPlaceholderText(burn_text(
            'LogPlaceholder', 'Encoding details will appear here after you start.'
        ))
        log_layout.addWidget(self.log_output)
        page.addWidget(log_card)
        page.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        action_card = CardWidget(self)
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(18, 12, 18, 12)
        action_layout.setSpacing(16)
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(6)
        status_row = QHBoxLayout()
        self.status_label = BodyLabel(burn_text('Ready', 'Ready'), action_card)
        self.percent_label = BodyLabel('0%', action_card)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        status_row.addWidget(self.percent_label)
        progress_layout.addLayout(status_row)
        self.progress_bar = ProgressBar(action_card)
        self.progress_bar.setRange(0, 100)
        progress_layout.addWidget(self.progress_bar)
        action_layout.addLayout(progress_layout, 1)
        self.run_button = PrimaryPushButton(burn_text('Start', 'Burn subtitles'), action_card)
        self.run_button.setIcon(FluentIcon.PLAY)
        self.open_folder_button = PushButton(
            burn_text('OpenFolder', 'Open folder'), action_card
        )
        self.open_folder_button.setIcon(FluentIcon.FOLDER)
        self.open_folder_button.setVisible(False)
        self.open_folder_button.clicked.connect(self._open_output_folder)
        self.cancel_button = PushButton(burn_text('Cancel', 'Cancel'), action_card)
        self.cancel_button.setVisible(False)
        action_layout.addWidget(self.open_folder_button)
        action_layout.addWidget(self.run_button)
        action_layout.addWidget(self.cancel_button)
        self.run_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self._cancel)
        root.addWidget(action_card)

    @staticmethod
    def _add_field(grid, row, column, label, widget):
        field = QVBoxLayout()
        field.setSpacing(5)
        field.addWidget(BodyLabel(label))
        field.addWidget(widget)
        grid.addLayout(field, row, column)

    def _clear_auto_output(self, *_args):
        self._auto_output_path = None

    def _choose_input(self, edit, filters):
        selected, _ = QFileDialog.getOpenFileName(self, burn_text('ChooseFile', 'Choose file'), edit.text() or '.', filters)
        if selected:
            edit.setText(selected)
            if edit is self.video_edit:
                self._suggest_output()

    def _choose_output(self, edit, filters):
        selected, _ = QFileDialog.getSaveFileName(self, burn_text('ChooseOutput', 'Choose output'), edit.text() or '.', filters)
        if selected:
            edit.setText(selected)

    def _suggest_output(self):
        source = self.video_edit.text().strip()
        current = self.output_edit.text().strip()
        if source and (not current or current == self._auto_output_path):
            path = Path(source)
            self._auto_output_path = str(path.with_name(f'{path.stem}_subtitled.mp4'))
            self.output_edit.setText(self._auto_output_path)

    def _choose_color(self):
        color = QColorDialog.getColor(QColor(self.color_edit.text()), self)
        if color.isValid():
            self.color_edit.setText(color.name().upper())

    def _update_style_state(self, path):
        enabled = not path.strip().lower().endswith('.ass')
        for widget in (
            self.font_edit, self.font_size, self.margin_bottom,
            self.outline, self.shadow, self.color_edit, self.color_button,
        ):
            widget.setEnabled(enabled)

    def _update_bitrate_mode_state(self, *_args):
        quality_mode = self.bitrate_mode.currentData() == 'quality'
        self.quality.setEnabled(quality_mode)

    def _set_running(self, running):
        self.run_button.setVisible(not running)
        self.cancel_button.setVisible(running)
        for widget in (
            self.video_row, self.subtitle_row, self.output_row,
            self.encoder, self.bitrate_mode, self.quality,
        ):
            widget.setEnabled(not running)
        if running:
            for widget in (
                self.font_edit, self.font_size, self.margin_bottom,
                self.outline, self.shadow, self.color_edit, self.color_button,
            ):
                widget.setEnabled(False)
        else:
            self._update_style_state(self.subtitle_edit.text())
            self._update_bitrate_mode_state()

    def _options(self):
        return BurnOptions(
            video_path=self.video_edit.text().strip(),
            subtitle_path=self.subtitle_edit.text().strip(),
            output_path=self.output_edit.text().strip(),
            font_name=self.font_edit.text().strip(),
            font_size=self.font_size.value(),
            margin_bottom=self.margin_bottom.value(),
            outline=self.outline.value(),
            shadow=self.shadow.value(),
            color=self.color_edit.text().strip(),
            encoder=self.encoder.currentData(),
            bitrate_mode=self.bitrate_mode.currentData(),
            quality=self.quality.value(),
        )

    def _start(self):
        options = self._options()
        for path, widget, label in (
            (options.video_path, self.video_edit, burn_text('InputVideo', 'Input video')),
            (options.subtitle_path, self.subtitle_edit, burn_text('SubtitleFile', 'Subtitle')),
            (options.output_path, self.output_edit, burn_text('OutputVideo', 'Output video')),
        ):
            if not path:
                widget.setFocus()
                InfoBar.warning(
                    burn_text('MissingFile', 'Please choose a file'), label,
                    duration=4000, parent=self,
                )
                return
        if os.path.exists(options.output_path):
            answer = QMessageBox.question(
                self,
                burn_text('OverwriteTitle', 'Replace output?'),
                burn_text('OverwriteMessage', 'The output file already exists. Replace it?'),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.progress_bar.setValue(0)
        self._completed_path = None
        self.open_folder_button.setVisible(False)
        self.percent_label.setText('0%')
        self.status_label.setText(burn_text('Preparing', 'Preparing...'))
        self.log_output.clear()
        self._set_running(True)
        self._worker = SubtitleBurner(
            options,
            progress=self.progress_signal.emit,
            log=self.log_signal.emit,
        )

        def run():
            try:
                self.completed_signal.emit(self._worker.run())
            except BurnCancelled:
                self.cancelled_signal.emit()
            except Exception as error:
                self.failed_signal.emit(str(error))

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def _cancel(self):
        if self._worker:
            self.cancel_button.setEnabled(False)
            self.log_signal.emit(burn_text('Cancelling', 'Cancelling...'))
            self._worker.cancel()

    def _append_log(self, message):
        self.log_output.appendPlainText(message.strip())
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _open_output_folder(self):
        if self._completed_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(
                os.path.dirname(self._completed_path)
            ))

    def _on_progress(self, value):
        value = max(0, min(100, int(value)))
        self.progress_bar.setValue(value)
        self.percent_label.setText(f'{value}%')
        self.status_label.setText(burn_text(
            'Saving' if value >= 99 else 'Running',
            'Saving output...' if value >= 99 else 'Burning subtitles...',
        ))

    def _finish(self):
        self._worker = None
        self._thread = None
        self.cancel_button.setEnabled(True)
        self._set_running(False)

    def _on_completed(self, result):
        self._finish()
        self._completed_path = result.output_path
        self.open_folder_button.setVisible(True)
        self.progress_bar.setValue(100)
        self.percent_label.setText('100%')
        self.status_label.setText(burn_text('Completed', 'Completed'))
        details = burn_text('CompletedDetails', 'Encoder: {encoder}; audio: {audio}').format(
            encoder=result.encoder, audio=result.audio_codec
        )
        self._append_log(f'{burn_text("Completed", "Completed")}: {result.output_path}\n{details}')
        InfoBar.success(
            burn_text('Completed', 'Completed'), result.output_path,
            duration=5000, parent=self,
        )

    def _on_failed(self, message):
        self._finish()
        self.status_label.setText(burn_text('Error', 'Subtitle burn failed'))
        self._append_log(message)
        InfoBar.error(
            burn_text('Error', 'Subtitle burn failed'), message,
            duration=8000, parent=self,
        )

    def _on_cancelled(self):
        self._finish()
        self.status_label.setText(burn_text('Cancelled', 'Cancelled'))
        self._append_log(burn_text('Cancelled', 'Cancelled'))
