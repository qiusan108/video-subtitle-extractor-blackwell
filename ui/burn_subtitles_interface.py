"""Desktop interface for burning SRT/ASS subtitles into a video."""

import os
from pathlib import Path
import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CardWidget, FluentIcon, InfoBar, PlainTextEdit, ProgressBar, PushButton

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
        self._build_ui()
        self.progress_signal.connect(self.progress_bar.setValue)
        self.log_signal.connect(self._append_log)
        self.completed_signal.connect(self._on_completed)
        self.failed_signal.connect(self._on_failed)
        self.cancelled_signal.connect(self._on_cancelled)

    def _path_row(self, button_text, save=False, filters='All Files (*)'):
        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(widget)
        button = PushButton(button_text, widget)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        if save:
            button.clicked.connect(lambda: self._choose_output(edit, filters))
        else:
            button.clicked.connect(lambda: self._choose_input(edit, filters))
        return widget, edit

    def _build_ui(self):
        self.setContentsMargins(16, 16, 16, 16)
        root = QVBoxLayout(self)
        root.setSpacing(12)

        files_card = CardWidget(self)
        files_form = QFormLayout(files_card)
        files_form.setContentsMargins(16, 16, 16, 16)
        self.video_row, self.video_edit = self._path_row(
            burn_text('Browse', 'Browse'),
            filters='Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;All Files (*)',
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
        files_form.addRow(burn_text('InputVideo', 'Input video'), self.video_row)
        files_form.addRow(burn_text('SubtitleFile', 'Subtitle (SRT/ASS)'), self.subtitle_row)
        files_form.addRow(burn_text('OutputVideo', 'Output video'), self.output_row)
        root.addWidget(files_card)

        options_card = CardWidget(self)
        options_form = QFormLayout(options_card)
        options_form.setContentsMargins(16, 16, 16, 16)
        self.font_edit = QLineEdit('Microsoft YaHei', options_card)
        self.font_size = QSpinBox(options_card)
        self.font_size.setRange(8, 200)
        self.font_size.setValue(48)
        self.margin_bottom = QSpinBox(options_card)
        self.margin_bottom.setRange(0, 1000)
        self.margin_bottom.setValue(40)
        self.outline = QDoubleSpinBox(options_card)
        self.outline.setRange(0, 20)
        self.outline.setSingleStep(0.5)
        self.outline.setValue(2)
        self.shadow = QDoubleSpinBox(options_card)
        self.shadow.setRange(0, 20)
        self.shadow.setSingleStep(0.5)
        self.shadow.setValue(1)
        self.color_edit = QLineEdit('#FFFFFF', options_card)
        self.color_button = PushButton(burn_text('ChooseColor', 'Choose color'), options_card)
        color_row = QWidget(options_card)
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.addWidget(self.color_edit, 1)
        color_layout.addWidget(self.color_button)
        self.color_button.clicked.connect(self._choose_color)
        self.encoder = QComboBox(options_card)
        self.encoder.addItem(burn_text('EncoderAuto', 'Auto (prefer NVIDIA)'), 'auto')
        self.encoder.addItem('CPU H.264 (libx264)', 'libx264')
        self.encoder.addItem('CPU H.265 (libx265)', 'libx265')
        self.encoder.addItem('NVIDIA H.264 (h264_nvenc)', 'h264_nvenc')
        self.encoder.addItem('NVIDIA H.265 (hevc_nvenc)', 'hevc_nvenc')
        self.quality = QSpinBox(options_card)
        self.quality.setRange(0, 51)
        self.quality.setValue(20)
        self.quality.setToolTip(burn_text(
            'QualityHint', 'Lower values improve quality and increase file size.'
        ))
        options_form.addRow(burn_text('Font', 'Font'), self.font_edit)
        options_form.addRow(burn_text('FontSize', 'Font size'), self.font_size)
        options_form.addRow(burn_text('BottomMargin', 'Bottom margin'), self.margin_bottom)
        options_form.addRow(burn_text('Outline', 'Outline'), self.outline)
        options_form.addRow(burn_text('Shadow', 'Shadow'), self.shadow)
        options_form.addRow(burn_text('TextColor', 'Text color'), color_row)
        options_form.addRow(burn_text('Encoder', 'Video encoder'), self.encoder)
        options_form.addRow(burn_text('Quality', 'Quality (CRF/CQ)'), self.quality)
        self.ass_note = QLabel(burn_text(
            'AssStyleNote', 'ASS input keeps its embedded styles; SRT uses the style above.'
        ), options_card)
        self.ass_note.setWordWrap(True)
        options_form.addRow('', self.ass_note)
        root.addWidget(options_card)

        action_card = CardWidget(self)
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(16, 12, 16, 12)
        self.run_button = PushButton(burn_text('Start', 'Burn subtitles'), action_card)
        self.run_button.setIcon(FluentIcon.PLAY)
        self.cancel_button = PushButton(burn_text('Cancel', 'Cancel'), action_card)
        self.cancel_button.setVisible(False)
        self.progress_bar = ProgressBar(action_card)
        self.progress_bar.setRange(0, 100)
        action_layout.addWidget(self.run_button)
        action_layout.addWidget(self.cancel_button)
        action_layout.addWidget(self.progress_bar, 1)
        self.run_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self._cancel)
        root.addWidget(action_card)

        self.log_output = PlainTextEdit(self)
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(140)
        root.addWidget(self.log_output, 1)

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
        if source and not self.output_edit.text().strip():
            path = Path(source)
            self.output_edit.setText(str(path.with_name(f'{path.stem}_subtitled.mp4')))

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

    def _set_running(self, running):
        self.run_button.setVisible(not running)
        self.cancel_button.setVisible(running)
        for widget in (
            self.video_row, self.subtitle_row, self.output_row,
            self.encoder, self.quality,
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
            quality=self.quality.value(),
        )

    def _start(self):
        options = self._options()
        if os.path.exists(options.output_path):
            answer = QMessageBox.question(
                self,
                burn_text('OverwriteTitle', 'Replace output?'),
                burn_text('OverwriteMessage', 'The output file already exists. Replace it?'),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.progress_bar.setValue(0)
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

    def _finish(self):
        self._worker = None
        self._thread = None
        self.cancel_button.setEnabled(True)
        self._set_running(False)

    def _on_completed(self, result):
        self._finish()
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
        self._append_log(message)
        InfoBar.error(
            burn_text('Error', 'Subtitle burn failed'), message,
            duration=8000, parent=self,
        )

    def _on_cancelled(self):
        self._finish()
        self._append_log(burn_text('Cancelled', 'Cancelled'))
