import logging
import os
import qt
import vtk
import slicer
from slicer.i18n import tr as _
from SegmentEditorEffects import *
import slicer.util


class SegmentEditorEffect(AbstractScriptedSegmentEditorLabelEffect):
    """DragEffect is a LabelEffect implementing interactive segment translation."""

    def __init__(self, scriptedEffect):
        scriptedEffect.name = "Drag"
        scriptedEffect.title = _("Drag")
        self.dragPipelines = {}
        AbstractScriptedSegmentEditorLabelEffect.__init__(self, scriptedEffect)
        self.startRAS = None

    def clone(self):
        import qSlicerSegmentationsEditorEffectsPythonQt as effects

        clonedEffect = effects.qSlicerSegmentEditorScriptedLabelEffect(None)
        clonedEffect.setPythonSource(__file__.replace("\\", "/"))
        return clonedEffect

    def icon(self):
        iconPath = os.path.join(os.path.dirname(__file__), 'SegmentEditorEffect.png')
        if os.path.exists(iconPath):
            return qt.QIcon(iconPath)
        return qt.QIcon()

    def helpText(self):
        return "<html>" + _("""Drag segment in slice viewers<br>
<p><ul style="margin: 0">
<li><b>Left-button drag-and-drop:</b> move the selected segment.
<li><b>Right-click:</b> cancel the drag preview.
</ul></p>
<p><b>Copy to frames (Sequences):</b><br>
If the segmentation is part of a sequence, you can copy the <b>current segment</b> to a frame range using the <b>Copy</b> button.</p>
</html>""")

    def createCursor(self, widget):
      return qt.QCursor(qt.Qt.OpenHandCursor)

    def setupOptionsFrame(self):
      layout = self.scriptedEffect.optionsFrame().layout()
      self.propagateButton = qt.QPushButton(_("Copy"))
      self.propagateButton.enabled = False
      layout.addWidget(self.propagateButton)
      self.propagateButton.clicked.connect(self.onPropagateButtonClicked)
      rangeWidget = qt.QWidget()
      rangeLayout = qt.QHBoxLayout(rangeWidget)
      rangeLayout.setContentsMargins(0, 0, 0, 0)
      self.startFrameSpinBox = qt.QSpinBox()
      self.startFrameSpinBox.setMinimum(0)
      self.startFrameSpinBox.setPrefix(_("Start: "))
      rangeLayout.addWidget(self.startFrameSpinBox)
      self.endFrameSpinBox = qt.QSpinBox()
      self.endFrameSpinBox.setMinimum(0)
      self.endFrameSpinBox.setPrefix(_("End: "))
      rangeLayout.addWidget(self.endFrameSpinBox)
      layout.addRow(rangeWidget)
      self.startFrameSpinBox.enabled = False
      self.endFrameSpinBox.enabled = False
      self.updatePropagationUi()
      self._endEdited = False
      self.endFrameSpinBox.valueChanged.connect(self._onEndChanged)
      self.endFrameSpinBox.valueChanged.connect(lambda _: setattr(self, "_endEdited", True))

    def pipelineForWidget(self, sliceWidget):
        if sliceWidget in self.dragPipelines:
            return self.dragPipelines[sliceWidget]

        pipeline = DragPipeline(self.scriptedEffect, sliceWidget)
        renderer = self.scriptedEffect.renderer(sliceWidget)
        if renderer is None:
            logging.error("pipelineForWidget: Failed to get renderer!")
            return None
        self.scriptedEffect.addActor2D(sliceWidget, pipeline.actor)
        self.dragPipelines[sliceWidget] = pipeline
        return pipeline

    def _onEndChanged(self, value):
      if not hasattr(self, "startFrameSpinBox"):
        return
      startVal = self.startFrameSpinBox.value
      if value < startVal:
        self.endFrameSpinBox.blockSignals(True)
        self.endFrameSpinBox.value = startVal
        self.endFrameSpinBox.blockSignals(False)

    def processInteractionEvents(self, callerInteractor, eventId, viewWidget):
        abortEvent = False
        if viewWidget.className() != "qMRMLSliceWidget":
            return abortEvent

        pipeline = self.pipelineForWidget(viewWidget)
        if pipeline is None:
            return abortEvent

        anyModifierKeyPressed = callerInteractor.GetShiftKey() or callerInteractor.GetControlKey() or callerInteractor.GetAltKey()

        if eventId == vtk.vtkCommand.LeftButtonPressEvent and not anyModifierKeyPressed:
            # Start drag
            confirmedEditingAllowed = self.scriptedEffect.confirmCurrentSegmentVisible()
            if confirmedEditingAllowed in [self.scriptedEffect.NotConfirmed,
                                           self.scriptedEffect.ConfirmedWithDialog]:
                abortEvent = True
                return abortEvent

            pipeline.actionState = "dragging"
            viewWidget.setCursor(qt.QCursor(qt.Qt.ClosedHandCursor))
            self.startRAS = self.xyToRas(callerInteractor.GetEventPosition(), viewWidget)
            # Store initial geometry of the selected segment
            segmentationNode = self.scriptedEffect.parameterSetNode().GetSegmentationNode()
            selectedSegmentID = self.scriptedEffect.parameterSetNode().GetSelectedSegmentID()
            segmentPolyData = vtk.vtkPolyData()
            slicer.vtkSlicerSegmentationsModuleLogic().GetSegmentClosedSurfaceRepresentation(
                segmentationNode, selectedSegmentID, segmentPolyData
            )
            pipeline.addPoints(segmentPolyData)
            abortEvent = True

        elif eventId == vtk.vtkCommand.MouseMoveEvent and pipeline.actionState == "dragging":
            # Update drag preview
            currentRAS = self.xyToRas(callerInteractor.GetEventPosition(), viewWidget)
            translation = [currentRAS[i] - self.startRAS[i] for i in range(3)]

            pipeline.updatePreview(translation)
            abortEvent = True
        elif eventId == vtk.vtkCommand.LeftButtonReleaseEvent and pipeline.actionState == "dragging":
            # Finish drag
            segmentationNode = self.scriptedEffect.parameterSetNode().GetSegmentationNode()
            selectedSegmentID = self.scriptedEffect.parameterSetNode().GetSelectedSegmentID()
            pipeline.apply(segmentationNode, selectedSegmentID)
            pipeline.actionState = ""
            viewWidget.setCursor(qt.QCursor(qt.Qt.OpenHandCursor))
            abortEvent = True

        elif eventId == vtk.vtkCommand.RightButtonPressEvent and pipeline.actionState == "dragging":
            # Cancel with right-click
            pipeline.cancelPreview()
            pipeline.actionState = ""
            viewWidget.setCursor(qt.QCursor(qt.Qt.OpenHandCursor))
            abortEvent = True

        else:
            pass

        pipeline.positionActors()
        return abortEvent

    def getSequenceAndBrowserForSegmentation(self, segmentationNode):
        if not segmentationNode:
            return None, None

        scene = slicer.mrmlScene
        browsers = scene.GetNodesByClass("vtkMRMLSequenceBrowserNode")
        browsers.UnRegister(scene)

        for i in range(browsers.GetNumberOfItems()):
            browser = browsers.GetItemAsObject(i)

            sequenceNodes = vtk.vtkCollection()
            browser.GetSynchronizedSequenceNodes(sequenceNodes)

            for j in range(sequenceNodes.GetNumberOfItems()):
                sequenceNode = sequenceNodes.GetItemAsObject(j)

                proxy = browser.GetProxyNode(sequenceNode)
                if proxy == segmentationNode:
                    return sequenceNode, browser

        return None, None

    def onPropagateButtonClicked(self):
      parameterNode = self.scriptedEffect.parameterSetNode()
      segmentationNode = parameterNode.GetSegmentationNode()
      segmentID = parameterNode.GetSelectedSegmentID()
      if not segmentationNode or not segmentID:
        return
      sequenceNode, browser = self.getSequenceAndBrowserForSegmentation(segmentationNode)
      if not sequenceNode:
        return
      if not browser:
        return
      sourceIndex = browser.GetSelectedItemNumber()

      self.scriptedEffect.saveStateForUndo()
      self.propagateSegmentOverRange(
          sequenceNode,
          sourceIndex,
          self.startFrameSpinBox.value,
          self.endFrameSpinBox.value,
          segmentID
      )

    def propagateSegmentOverRange(
        self,
        sequenceNode,
        sourceIndex,
        startIndex,
        endIndex,
        segmentID
    ):
      logic = slicer.vtkSlicerSegmentationsModuleLogic()
      sourceSegmentation = sequenceNode.GetNthDataNode(sourceIndex)
      if not sourceSegmentation:
        return
      # Extract source segment labelmap
      sourceLabelmap = slicer.vtkOrientedImageData()
      logic.GetSegmentBinaryLabelmapRepresentation(
          sourceSegmentation,
          segmentID,
          sourceLabelmap
      )
      for i in range(startIndex, endIndex + 1):
        if i == sourceIndex:
            continue
        targetSegmentation = sequenceNode.GetNthDataNode(i)
        if not targetSegmentation:
            continue
        if not targetSegmentation.GetSegmentation().GetSegment(segmentID):
            continue
        copiedLabelmap = slicer.vtkOrientedImageData()
        copiedLabelmap.DeepCopy(sourceLabelmap)
        logic.SetBinaryLabelmapToSegment(
            copiedLabelmap,
            targetSegmentation,
            segmentID,
            logic.MODE_REPLACE
        )

    def updatePropagationUi(self):
      # Options frame may not be built yet
      if not (hasattr(self, "propagateButton") and hasattr(self, "startFrameSpinBox") and hasattr(self, "endFrameSpinBox")):
        return

      parameterNode = self.scriptedEffect.parameterSetNode()
      if not parameterNode:
        self.propagateButton.enabled = False
        self.startFrameSpinBox.enabled = False
        self.endFrameSpinBox.enabled = False
        return

      segmentationNode = parameterNode.GetSegmentationNode()
      if not segmentationNode:
        self.propagateButton.enabled = False
        self.startFrameSpinBox.enabled = False
        self.endFrameSpinBox.enabled = False
        return

      sequenceNode, browser = self.getSequenceAndBrowserForSegmentation(segmentationNode)

      if not (sequenceNode and browser):
        # Disable everything if no corresponding sequence/browser
        self.propagateButton.enabled = False
        self.startFrameSpinBox.enabled = False
        self.endFrameSpinBox.enabled = False
        self._endEdited = False
        return

      if getattr(self, "_observedBrowserNode", None) != browser:
        self._observedBrowserNode = browser
        self._endEdited = False

      self.propagateButton.enabled = True
      self.startFrameSpinBox.enabled = True
      self.endFrameSpinBox.enabled = True
      endIndex = sequenceNode.GetNumberOfDataNodes() - 1
      self.startFrameSpinBox.maximum = endIndex
      self.endFrameSpinBox.maximum = endIndex
      sourceIndex = browser.GetSelectedItemNumber()
      defaultStart = min(sourceIndex + 1, endIndex)
      defaultEnd = endIndex
      self.startFrameSpinBox.blockSignals(True)
      self.startFrameSpinBox.value = defaultStart
      self.startFrameSpinBox.blockSignals(False)
      if not getattr(self, "_endEdited", False):
        self.endFrameSpinBox.blockSignals(True)
        self.endFrameSpinBox.value = defaultEnd
        self.endFrameSpinBox.blockSignals(False)
      endVal = self.endFrameSpinBox.value
      if endVal < defaultStart:
        self.endFrameSpinBox.blockSignals(True)
        self.endFrameSpinBox.value = defaultStart
        self.endFrameSpinBox.blockSignals(False)
      elif endVal > endIndex:
        self.endFrameSpinBox.blockSignals(True)
        self.endFrameSpinBox.value = endIndex
        self.endFrameSpinBox.blockSignals(False)

    def activate(self):
      self._parameterNodeObserver = None
      p = self.scriptedEffect.parameterSetNode()
      if p:
        self._parameterNodeObserver = p.AddObserver(vtk.vtkCommand.ModifiedEvent, self._onParameterNodeModified)
      self.updatePropagationUi()
      # Observe sequence browser selection change (timepoint change)
      self._browserObserver = None
      parameterNode = self.scriptedEffect.parameterSetNode()
      segNode = parameterNode.GetSegmentationNode() if parameterNode else None
      seqNode, browser = self.getSequenceAndBrowserForSegmentation(segNode)
      self._observedBrowserNode = browser
      if browser:
        self._browserObserver = browser.AddObserver(vtk.vtkCommand.ModifiedEvent, self._onParameterNodeModified)

    def deactivate(self):
      for sliceWidget, pipeline in self.dragPipelines.items():
        self.scriptedEffect.removeActor2D(sliceWidget, pipeline.actor)
      self.dragPipelines = {}
      p = self.scriptedEffect.parameterSetNode()
      if p and hasattr(self, "_parameterNodeObserver") and self._parameterNodeObserver:
        p.RemoveObserver(self._parameterNodeObserver)
        self._parameterNodeObserver = None
      # remove browser observer
      if hasattr(self, "_observedBrowserNode") and self._observedBrowserNode and hasattr(self, "_browserObserver") and self._browserObserver:
        self._observedBrowserNode.RemoveObserver(self._browserObserver)
        self._browserObserver = None
        self._observedBrowserNode = None

    def _onParameterNodeModified(self, caller=None, event=None):
      self.updatePropagationUi()





#
# DragPipeline
#
class DragPipeline:
    """Visualization objects and pipeline for each slice view for dragging"""

    def __init__(self, scriptedEffect, sliceWidget):
        self.scriptedEffect = scriptedEffect
        self.sliceWidget = sliceWidget
        self.activeSliceOffset = None
        self.lastInsertSliceNodeMTime = None
        self.actionState = ""

        self.polyData = vtk.vtkPolyData()

        # Store the original geometry and preview data
        self.originalSegmentPolyData = vtk.vtkPolyData()

        # Create a mapper and actor for preview
        self.mapper = vtk.vtkPolyDataMapper2D()
        self.actor = vtk.vtkActor2D()
        self.mapper.SetInputData(self.polyData)
        self.actor.SetMapper(self.mapper)
        actorProperty = self.actor.GetProperty()
        actorProperty.SetColor(1, 1, 0)
        actorProperty.SetLineWidth(2)
        #
        self.dragTransform = vtk.vtkTransform()
        self.transformFilter = vtk.vtkTransformPolyDataFilter()
        # self.transformFilter.SetInputData(self.originalSegmentPolyData)
        self.transformFilter.SetTransform(self.dragTransform)
        # self.transformFilter.Update()

        self.translationVectorRAS = [0.0, 0.0, 0.0]

    def addPoints(self, RASpolyData):
        """Store the starting geometry of the selected segment."""
        sliceLogic = self.sliceWidget.sliceLogic()
        currentSliceOffset = sliceLogic.GetSliceOffset()
        if not self.activeSliceOffset:
            self.activeSliceOffset = currentSliceOffset

        if self.activeSliceOffset != currentSliceOffset:
            return

        sliceNode = sliceLogic.GetSliceNode()
        self.lastInsertSliceNodeMTime = sliceNode.GetMTime()

        # Get original RAS points on the current slice
        sliceToRAS = sliceNode.GetSliceToRAS()
        origin = sliceToRAS.MultiplyPoint([0,0,0,1])[:3]
        normal = sliceToRAS.MultiplyPoint([0,0,1,0])[:3]
        plane = vtk.vtkPlane()
        plane.SetOrigin(origin)
        plane.SetNormal(normal)
        cutter = vtk.vtkCutter()
        cutter.SetInputData(RASpolyData)
        cutter.SetCutFunction(plane)
        cutter.Update()
        cutterOut = cutter.GetOutput()
        ptsRAS = cutterOut.GetPoints()
        if ptsRAS is None or ptsRAS.GetNumberOfPoints() == 0:
            logging.debug("DragPipeline.addPoints: cutter returned no points for this slice")
            self.polyData.GetLines().Reset()
            self.polyData.Modified()
            self.sliceWidget.sliceView().scheduleRender()
            return

        sliceLogic = self.sliceWidget.sliceLogic()
        sliceNode = sliceLogic.GetSliceNode()
        rasToXY = vtk.vtkTransform()
        rasToXY.SetMatrix(sliceNode.GetXYToRAS())
        rasToXY.Inverse()

        transformFilter = vtk.vtkTransformPolyDataFilter()
        transformFilter.SetInputData(cutterOut)
        transformFilter.SetTransform(rasToXY)
        transformFilter.Update()
        slicePD = transformFilter.GetOutput()

        self.originalSegmentPolyData.DeepCopy(cutterOut)
        self.polyData.DeepCopy(slicePD)
        self.transformFilter.SetInputData(self.originalSegmentPolyData)
        self.transformFilter.Update()

        self.polyData.Modified()
        self.sliceWidget.sliceView().scheduleRender()


    def updatePreview(self, translationVectorRAS):
        """Update preview geometry when dragging."""
        self.translationVectorRAS = list(translationVectorRAS)

        self.dragTransform.Identity()
        self.dragTransform.Translate(translationVectorRAS)
        self.transformFilter.SetTransform(self.dragTransform)
        self.transformFilter.Update()
        RASslicepolyData = self.transformFilter.GetOutput()

        sliceLogic = self.sliceWidget.sliceLogic()
        sliceNode = sliceLogic.GetSliceNode()
        rasToXY = vtk.vtkTransform()
        rasToXY.SetMatrix(sliceNode.GetXYToRAS())
        rasToXY.Inverse()

        transformFilter = vtk.vtkTransformPolyDataFilter()
        transformFilter.SetInputData(RASslicepolyData)
        transformFilter.SetTransform(rasToXY)
        transformFilter.Update()
        slicePD = transformFilter.GetOutput()

        self.polyData.DeepCopy(slicePD)
        self.polyData.Modified()
        self.sliceWidget.sliceView().scheduleRender()

    def positionActors(self):
        """Ensure preview follows slice view."""
        self.polyData.Modified()
        self.sliceWidget.sliceView().scheduleRender()

    def apply(self, segmentationNode, selectedSegmentID):
        """Commit the translated geometry to the segmentation node."""

        segmentation = segmentationNode.GetSegmentation()

        segmentation.SeparateSegmentLabelmap(selectedSegmentID)

        segLabelmap = slicer.vtkOrientedImageData()
        slicer.vtkSlicerSegmentationsModuleLogic().GetSegmentBinaryLabelmapRepresentation(
                segmentationNode, selectedSegmentID, segLabelmap
        )

        # # Build a world-space/ RAS translation transform
        transform = vtk.vtkTransform()
        transform.Translate(self.translationVectorRAS)

        slicer.vtkOrientedImageDataResample.TransformOrientedImage(
                        segLabelmap,
                        transform
                    )

        # self.scriptedEffect.saveStateForUndo()
        #
        # slicer.vtkSlicerSegmentationsModuleLogic.SetBinaryLabelmapToSegment(
        #     segLabelmap, segmentationNode, selectedSegmentID, slicer.vtkSlicerSegmentationsModuleLogic.MODE_REPLACE
        # )
        self.scriptedEffect.saveStateForUndo()
        slicer.vtkSlicerSegmentationsModuleLogic.SetBinaryLabelmapToSegment(
            segLabelmap,
            segmentationNode,
            selectedSegmentID,
            slicer.vtkSlicerSegmentationsModuleLogic.MODE_REPLACE
        )

        self.resetGeometry()
        self.sliceWidget.sliceView().scheduleRender()


    def resetGeometry(self):
        """Reset to initial state, remove preview."""
        self.originalSegmentPolyData.Initialize()
        self.polyData.Initialize()
        self.dragTransform.Identity()
        self.translationVectorRAS = [0.0, 0.0, 0.0]
        self.activeSliceOffset = None

    def cancelPreview(self):
        """Abort drag and reset preview to original geometry."""
        sliceLogic = self.sliceWidget.sliceLogic()
        sliceNode = sliceLogic.GetSliceNode()
        rasToXY = vtk.vtkTransform()
        rasToXY.SetMatrix(sliceNode.GetXYToRAS())
        rasToXY.Inverse()

        transformFilter = vtk.vtkTransformPolyDataFilter()
        transformFilter.SetInputData(self.originalSegmentPolyData)
        transformFilter.SetTransform(rasToXY)
        transformFilter.Update()
        slicePD = transformFilter.GetOutput()

        self.polyData.DeepCopy(slicePD)
        self.polyData.Modified()
        self.sliceWidget.sliceView().scheduleRender()
