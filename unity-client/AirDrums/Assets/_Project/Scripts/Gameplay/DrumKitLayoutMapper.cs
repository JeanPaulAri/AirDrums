using System;
using System.Collections.Generic;
using UnityEngine;

public class DrumKitLayoutMapper : MonoBehaviour
{
    [Header("Pad References")]
    [SerializeField] private Transform padCrash;
    [SerializeField] private Transform padTom;
    [SerializeField] private Transform padHiHat;
    [SerializeField] private Transform padSnare;
    [SerializeField] private Transform padFloorTom;
    [SerializeField] private Transform padKick;

    [Header("Projection References")]
    [SerializeField] private Camera projectionCamera;
    [SerializeField] private Transform drumRoot;
    [SerializeField] private Transform projectionAnchor;
    [SerializeField] private TrackingDebugOverlay debugOverlay;

    [Header("Projection Depth")]
    [SerializeField] private bool useAnchorDepth = true;
    [SerializeField] private float manualProjectionDepth = 7.8f;

    [Header("Visual Offsets")]
    [SerializeField] private Vector3 upperArcLocalOffset = new Vector3(0f, 0.02f, 0.08f);
    [SerializeField] private Vector3 lowerArcLocalOffset = new Vector3(0f, 0.00f, 0.02f);
    [SerializeField] private Vector3 kickLocalOffset = new Vector3(0f, -0.10f, -0.02f);

    [Header("Perfiles base tomados de main.py")]
    [SerializeField] private float referenceWidth = 110f;
    [SerializeField] private float referenceHeight = 50f;

    [Header("Logical Zone / HitZone")]
    [SerializeField] private bool applyLogicalZoneProfiles = true;
    [SerializeField] private bool applyMeshProfiles = true;
    [SerializeField] private bool applyHitZoneProfiles = true;
    [SerializeField] private float hitZoneScaleFactor = 0.8f;
    [SerializeField] private float handHitZoneLiftY = 0.04f;
    [SerializeField] private float kickHitZoneForwardOffset = 0.08f;

    [Header("Debug")]
    [SerializeField] private bool logAppliedLayout = true;
    
    [SerializeField] private Transform kickVisualAnchor;
    [SerializeField] private bool placeKickVisualUnderLowerArc = true;
    [SerializeField] private float kickVisualDropY = -0.34f;
    [SerializeField] private float kickVisualForwardOffset = 0.16f;
    [SerializeField] private float kickVisualXOffset = 0.00f;

    private readonly Dictionary<string, ZoneRuntimeState> runtime = new Dictionary<string, ZoneRuntimeState>();

    private void Awake()
    {
        if (drumRoot == null && padCrash != null && padCrash.parent != null)
        {
            drumRoot = padCrash.parent;
        }

        if (projectionCamera == null)
        {
            projectionCamera = Camera.main;
        }

        if (debugOverlay == null)
        {
            debugOverlay = FindObjectOfType<TrackingDebugOverlay>();
        }

        BuildRuntimeCache();
    }

    private void BuildRuntimeCache()
    {
        runtime.Clear();

        RegisterZone("crash", padCrash);
        RegisterZone("tom", padTom);
        RegisterZone("hihat", padHiHat);
        RegisterZone("snare", padSnare);
        RegisterZone("floor_tom", padFloorTom);
        RegisterZone("kick", padKick);
    }

    private void RegisterZone(string canonicalZone, Transform pad)
    {
        if (pad == null)
        {
            return;
        }

        Transform mesh = FindChildByPrefix(pad, "Mesh_");
        Transform hitZone = FindChildByPrefix(pad, "HitZone_");
        Transform logicalZone = FindChildByPrefix(pad, "LogicalZone_");

        runtime[canonicalZone] = new ZoneRuntimeState
        {
            pad = pad,
            mesh = mesh,
            hitZone = hitZone,
            logicalZone = logicalZone,

            meshBaseLocalPosition = mesh != null ? mesh.localPosition : Vector3.zero,
            meshBaseLocalRotation = mesh != null ? mesh.localRotation : Quaternion.identity,
            meshBaseLocalScale = mesh != null ? mesh.localScale : Vector3.one,

            hitZoneBaseLocalPosition = hitZone != null ? hitZone.localPosition : Vector3.zero,
            hitZoneBaseLocalRotation = hitZone != null ? hitZone.localRotation : Quaternion.identity,
            hitZoneBaseLocalScale = hitZone != null ? hitZone.localScale : Vector3.one,

            logicalZoneBaseLocalPosition = logicalZone != null ? logicalZone.localPosition : Vector3.zero,
            logicalZoneBaseLocalRotation = logicalZone != null ? logicalZone.localRotation : Quaternion.identity,
            logicalZoneBaseLocalScale = logicalZone != null ? logicalZone.localScale : Vector3.one
        };
    }

    public void ApplyConfiguration(MiddlewareConfigurationMessage message)
    {
        if (message == null)
        {
            Debug.LogWarning("[DrumKitLayoutMapper] Configuración nula.");
            return;
        }

        if (message.elementos == null || message.elementos.Length == 0)
        {
            Debug.LogWarning("[DrumKitLayoutMapper] Configuración sin elementos.");
            return;
        }

        if (projectionCamera == null || drumRoot == null || debugOverlay == null)
        {
            Debug.LogWarning("[DrumKitLayoutMapper] Faltan referencias: projectionCamera, drumRoot o debugOverlay.");
            return;
        }

        float projectionDepth = GetProjectionDepth();

        foreach (MiddlewareZoneElement element in message.elementos)
        {
            if (element == null || string.IsNullOrWhiteSpace(element.zone))
            {
                continue;
            }

            string rawZone = element.zone;
            string canonicalZone = NormalizeZoneToCanonical(rawZone);

            if (!runtime.TryGetValue(canonicalZone, out ZoneRuntimeState state) || state.pad == null)
            {
                continue;
            }

            Vector3 projectedLocal;
            if (debugOverlay.TryGetZoneLayout(rawZone, out TrackingDebugOverlay.ZoneOverlayLayout overlayLayout))
            {
                projectedLocal = ProjectOverlayViewportToKitLocal(overlayLayout.viewportCenter, projectionDepth);
            }
            else
            {
                projectedLocal = ProjectSourceFallbackToKitLocal(
                    element.x,
                    element.y,
                    Mathf.Max(1, message.dim_x),
                    Mathf.Max(1, message.dim_y),
                    projectionDepth
                );
            }

            Vector3 targetPadLocalPosition = projectedLocal + GetArcLocalOffset(canonicalZone);

            state.pad.localPosition = targetPadLocalPosition;
            state.pad.localRotation = GetPadRootRotation(canonicalZone);

            if (applyLogicalZoneProfiles)
            {
                ApplyLogicalZoneProfile(canonicalZone, state);
            }

            if (applyMeshProfiles)
            {
                ApplyMeshProfile(canonicalZone, state);
            }

            if (applyHitZoneProfiles)
            {
                ApplyHitZoneProfile(canonicalZone, state);
            }

            if (logAppliedLayout)
            {
                Debug.Log("[DrumKitLayoutMapper] zone=" + canonicalZone + " local=" + targetPadLocalPosition);
            }
        }
        
        PositionKickVisualUnderLowerArc();

    }

    private void PositionKickVisualUnderLowerArc()
    {
        if (!placeKickVisualUnderLowerArc || kickVisualAnchor == null || drumRoot == null || projectionCamera == null)
        {
            return;
        }

        List<Vector3> lowerArcPoints = new List<Vector3>();

        TryAddLowerArcPoint("hihat", lowerArcPoints);
        TryAddLowerArcPoint("snare", lowerArcPoints);
        TryAddLowerArcPoint("floor_tom", lowerArcPoints);

        if (lowerArcPoints.Count == 0)
        {
            return;
        }

        Vector3 lowerArcCenter = Vector3.zero;
        foreach (Vector3 p in lowerArcPoints)
        {
            lowerArcCenter += p;
        }
        lowerArcCenter /= lowerArcPoints.Count;

        Vector3 towardCameraLocal = drumRoot.InverseTransformDirection(projectionCamera.transform.forward);
        towardCameraLocal.y = 0f;

        if (towardCameraLocal.sqrMagnitude > 0.0001f)
        {
            towardCameraLocal.Normalize();
        }

        Vector3 finalPosition =
            lowerArcCenter +
            new Vector3(kickVisualXOffset, kickVisualDropY, 0f) +
            towardCameraLocal * kickVisualForwardOffset;

        kickVisualAnchor.localPosition = finalPosition;
    }

    private void TryAddLowerArcPoint(string canonicalZone, List<Vector3> points)
    {
        if (runtime.TryGetValue(canonicalZone, out ZoneRuntimeState state) && state.pad != null)
        {
            points.Add(state.pad.localPosition);
        }
    }
    
    private float GetProjectionDepth()
    {
        if (useAnchorDepth && projectionCamera != null && projectionAnchor != null)
        {
            Vector3 viewport = projectionCamera.WorldToViewportPoint(projectionAnchor.position);
            return Mathf.Max(0.1f, viewport.z);
        }

        return Mathf.Max(0.1f, manualProjectionDepth);
    }

    private Vector3 ProjectOverlayViewportToKitLocal(Vector2 viewportCenter, float projectionDepth)
    {
        Vector3 world = projectionCamera.ViewportToWorldPoint(
            new Vector3(viewportCenter.x, viewportCenter.y, projectionDepth)
        );

        return drumRoot.InverseTransformPoint(world);
    }

    private Vector3 ProjectSourceFallbackToKitLocal(float sourceX, float sourceY, int dimX, int dimY, float projectionDepth)
    {
        float nx = Mathf.Clamp01(sourceX / dimX);
        float ny = Mathf.Clamp01(sourceY / dimY);

        Vector3 world = projectionCamera.ViewportToWorldPoint(
            new Vector3(nx, 1f - ny, projectionDepth)
        );

        return drumRoot.InverseTransformPoint(world);
    }

    private Vector3 GetArcLocalOffset(string canonicalZone)
    {
        switch (canonicalZone)
        {
            case "crash":
            case "tom":
                return upperArcLocalOffset;

            case "hihat":
            case "snare":
            case "floor_tom":
                return lowerArcLocalOffset;

            case "kick":
                return kickLocalOffset;

            default:
                return Vector3.zero;
        }
    }

    private Quaternion GetPadRootRotation(string canonicalZone)
    {
        switch (canonicalZone)
        {
            case "crash": return Quaternion.Euler(-16f, 0f, 0f);
            case "tom": return Quaternion.Euler(-14f, 0f, 0f);
            case "hihat": return Quaternion.Euler(-14f, 0f, 0f);
            case "snare": return Quaternion.Euler(-10f, 0f, 0f);
            case "floor_tom": return Quaternion.Euler(-8f, 0f, 0f);
            case "kick": return Quaternion.identity;
            default: return Quaternion.identity;
        }
    }

    private void ApplyLogicalZoneProfile(string canonicalZone, ZoneRuntimeState state)
    {
        if (state.logicalZone == null)
        {
            return;
        }

        Vector2 sourceSize = GetZoneSourceSize(canonicalZone);
        float scaleX = sourceSize.x / referenceWidth;
        float scaleZ = sourceSize.y / referenceHeight;

        state.logicalZone.localPosition = Vector3.zero;
        state.logicalZone.localRotation = state.logicalZoneBaseLocalRotation;
        state.logicalZone.localScale = Multiply(
            state.logicalZoneBaseLocalScale,
            new Vector3(scaleX, 1f, scaleZ)
        );
    }

    private void ApplyMeshProfile(string canonicalZone, ZoneRuntimeState state)
    {
        if (canonicalZone == "kick")
        {
            return;
        }
        
        
        if (state.mesh == null)
        {
            return;
        }

        Vector2 sourceSize = GetZoneSourceSize(canonicalZone);
        float scaleX = sourceSize.x / referenceWidth;
        float scaleZ = sourceSize.y / referenceHeight;

        Vector3 zoneScale = GetMeshScaleMultiplier(canonicalZone);

        state.mesh.localScale = Multiply(
            state.meshBaseLocalScale,
            new Vector3(scaleX * zoneScale.x, zoneScale.y, scaleZ * zoneScale.z)
        );

        state.mesh.localRotation = state.meshBaseLocalRotation;
        state.mesh.localPosition = GetMeshLocalOffset(canonicalZone);
    }
    
    private Vector3 GetMeshLocalOffset(string canonicalZone)
    {
        switch (canonicalZone)
        {
            case "kick":
                // Subir el mesh del bombo sin mover la zona lógica
                return new Vector3(0f, 0.18f, 0f);

            default:
                return Vector3.zero;
        }
    }

    private void ApplyHitZoneProfile(string canonicalZone, ZoneRuntimeState state)
    {
        if (state.hitZone == null)
        {
            return;
        }

        if (state.logicalZone == null)
        {
            state.hitZone.localScale = state.hitZoneBaseLocalScale;
            state.hitZone.localRotation = state.hitZoneBaseLocalRotation;
            state.hitZone.localPosition = Vector3.zero;
            return;
        }

        float baseX = state.logicalZone.localScale.x;
        float baseZ = state.logicalZone.localScale.z;

        float hitX = Mathf.Max(0.05f, baseX * hitZoneScaleFactor);
        float hitZ = Mathf.Max(0.05f, baseZ * hitZoneScaleFactor);

        state.hitZone.localScale = new Vector3(
            state.hitZoneBaseLocalScale.x * hitX,
            state.hitZoneBaseLocalScale.y,
            state.hitZoneBaseLocalScale.z * hitZ
        );

        state.hitZone.localRotation = state.hitZoneBaseLocalRotation;

        if (canonicalZone == "kick")
        {
            Vector3 towardCameraLocal = drumRoot.InverseTransformDirection(projectionCamera.transform.forward);
            towardCameraLocal.y = 0f;
            if (towardCameraLocal.sqrMagnitude > 0.0001f)
            {
                towardCameraLocal.Normalize();
            }

            state.hitZone.localPosition = towardCameraLocal * kickHitZoneForwardOffset;
        }
        else
        {
            state.hitZone.localPosition = new Vector3(0f, handHitZoneLiftY, 0f);
        }
    }

    private Vector2 GetZoneSourceSize(string canonicalZone)
    {
        switch (canonicalZone)
        {
            case "crash": return new Vector2(110f, 60f);
            case "tom": return new Vector2(100f, 50f);
            case "hihat": return new Vector2(110f, 30f);
            case "snare": return new Vector2(140f, 50f);
            case "floor_tom": return new Vector2(110f, 30f);
            case "kick": return new Vector2(110f, 30f);
            default: return new Vector2(referenceWidth, referenceHeight);
        }
    }

    private Vector3 GetMeshScaleMultiplier(string canonicalZone)
    {
        switch (canonicalZone)
        {
            case "crash": return new Vector3(1.06f, 1.00f, 1.00f);
            case "tom": return new Vector3(1.00f, 1.00f, 1.00f);
            case "hihat": return new Vector3(1.02f, 1.00f, 0.92f);
            case "snare": return new Vector3(1.08f, 1.00f, 0.96f);
            case "floor_tom": return new Vector3(1.02f, 1.00f, 0.92f);
            case "kick": return new Vector3(1.00f, 1.00f, 0.95f);
            default: return Vector3.one;
        }
    }

    private Transform FindChildByPrefix(Transform parent, string prefix)
    {
        if (parent == null)
        {
            return null;
        }

        for (int i = 0; i < parent.childCount; i++)
        {
            Transform child = parent.GetChild(i);
            if (child != null && child.name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                return child;
            }
        }

        return null;
    }

    private string NormalizeZoneToCanonical(string zone)
    {
        string z = NormalizeZoneKey(zone);

        switch (z)
        {
            case "platillo": return "crash";
            case "tom superior": return "tom";
            case "hithat": return "hihat";
            case "tarola": return "snare";
            case "bombo": return "kick";
            case "tom inferior": return "floor_tom";

            case "crash": return "crash";
            case "tom": return "tom";
            case "hihat": return "hihat";
            case "snare": return "snare";
            case "kick": return "kick";
            case "floor_tom": return "floor_tom";

            default: return z;
        }
    }

    private string NormalizeZoneKey(string zone)
    {
        if (string.IsNullOrWhiteSpace(zone))
        {
            return string.Empty;
        }

        return string.Join(" ", zone.Trim().ToLowerInvariant().Split(' '));
    }

    private Vector3 Multiply(Vector3 a, Vector3 b)
    {
        return new Vector3(a.x * b.x, a.y * b.y, a.z * b.z);
    }

    private class ZoneRuntimeState
    {
        public Transform pad;
        public Transform mesh;
        public Transform hitZone;
        public Transform logicalZone;

        public Vector3 meshBaseLocalPosition;
        public Quaternion meshBaseLocalRotation;
        public Vector3 meshBaseLocalScale;

        public Vector3 hitZoneBaseLocalPosition;
        public Quaternion hitZoneBaseLocalRotation;
        public Vector3 hitZoneBaseLocalScale;

        public Vector3 logicalZoneBaseLocalPosition;
        public Quaternion logicalZoneBaseLocalRotation;
        public Vector3 logicalZoneBaseLocalScale;
    }
}