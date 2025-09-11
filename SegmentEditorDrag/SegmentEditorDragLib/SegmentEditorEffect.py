import logging
import os
import qt
import vtk
import slicer
from slicer.i18n import tr as _
from SegmentEditorEffects import *


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
        return "<html>" + _("""Drag segment in slice viewers<br>.
<p><ul style="margin: 0">
<li><b>Left-button drag-and-drop:</b> dragging taking place.
<li><b>x:</b> delete last point.
</ul><p>""")

    def deactivate(self):
        # Remove drag pipelines
        for sliceWidget, pipeline in self.dragPipelines.items():
            self.scriptedEffect.removeActor2D(sliceWidget, pipeline.actor)
        self.dragPipelines = {}

    def createCursor(self, widget):
      return qt.QCursor(qt.Qt.OpenHandCursor)

    def setupOptionsFrame(self):
        pass

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

        # segment = segmentation.GetSegment(selectedSegmentID)
        # if not segment:
        #     logging.warning(f"Segment {selectedSegmentID} not found")
        #     return

        # repName = slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
        # if not segment.GetRepresentation(repName):
        #     logging.warning(f"Segment {selectedSegmentID} has no binary labelmap representation")
        #     return
        #
        # segLabelmap = slicer.vtkOrientedImageData.SafeDownCast(segment.GetRepresentation(repName))
        # if segLabelmap is None:
        #     logging.warning(f"Failed to retrieve vtkOrientedImageData for segment {selectedSegmentID}")
        #     return
        #
        # if segLabelmap.GetDimensions()[0] == 0:
        #     # Segment is empty
        #     return

        # # Build a world-space/ RAS translation transform
        transform = vtk.vtkTransform()
        transform.Identity()
        transform.Translate(self.translationVectorRAS)
        # Invert
        inverseTransform = vtk.vtkTransform()
        inverseTransform.DeepCopy(transform)
        inverseTransform.Inverse()

        ijkToRasMatrix = vtk.vtkMatrix4x4()
        segLabelmap.GetImageToWorldMatrix(ijkToRasMatrix)
        rasToIjkMatrix = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(ijkToRasMatrix, rasToIjkMatrix)
        rasTransform = vtk.vtkTransform()
        rasTransform.Translate(self.translationVectorRAS)

        ijkTransform = vtk.vtkGeneralTransform()
        ijkTransform.Concatenate(rasToIjkMatrix)
        ijkTransform.Concatenate(rasTransform)
        ijkTransform.Concatenate(ijkToRasMatrix)
        ijkTransform.Inverse()


        slicer.vtkOrientedImageDataResample.TransformOrientedImage(
                        segLabelmap,
                        ijkTransform # transform
                    )

        # reslice = vtk.vtkImageReslice()
        # reslice.SetInputData(segLabelmap)
        # reslice.SetResliceTransform(ijkTransform)
        # reslice.SetInterpolationModeToNearestNeighbor()
        # # reslice.SetOutputSpacing(segLabelmap.GetSpacing())
        # # reslice.SetOutputOrigin(segLabelmap.GetOrigin())
        # # reslice.SetOutputExtent(segLabelmap.GetExtent())
        # reslice.Update()


        # reslicedImage = reslice.GetOutput()
        #
        # translatedLabelmap = slicer.vtkOrientedImageData()
        # translatedLabelmap.DeepCopy(reslicedImage)
        # translatedLabelmap.CopyDirections(segLabelmap)

        self.scriptedEffect.saveStateForUndo()
        # self.scriptedEffect.modifySelectedSegmentByLabelmap(translatedLabelmap, slicer.qSlicerSegmentEditorAbstractEffect.ModificationModeSet)

        slicer.vtkSlicerSegmentationsModuleLogic.SetBinaryLabelmapToSegment(
            segLabelmap, segmentationNode, selectedSegmentID, slicer.vtkSlicerSegmentationsModuleLogic.MODE_REPLACE
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
