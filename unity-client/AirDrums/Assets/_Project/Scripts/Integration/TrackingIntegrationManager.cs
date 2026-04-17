using System;
using System.Collections.Generic;
using UnityEngine;

public class TrackingIntegrationManager : MonoBehaviour
{
    [Header("References")]
    [SerializeField] private UdpReceiver udpReceiver;
    [SerializeField] private DrumKitManager drumKitManager;
    [SerializeField] private TrackingDebugOverlay debugOverlay;
    [SerializeField] private StickVisualizer rightStickVisualizer;
    [SerializeField] private StickVisualizer leftStickVisualizer;
    [SerializeField] private StickVisualizer footStickVisualizer;
    [SerializeField] private DrumKitLayoutMapper drumKitLayoutMapper;
    
    [Header("Debug")]
    [SerializeField] private bool logRawMessages = true;
    [SerializeField] private bool logParsedHits = true;
    [SerializeField] private bool enable3DStickMotion = true;

    [SerializeField] private bool showDebugOverlay = true;
    [SerializeField] private float overlayVisibleSeconds = 2f;
    
    private Coroutine overlayRoutine;
    private readonly Queue<Action> mainThreadActions = new Queue<Action>();
    private MiddlewareConfigurationMessage currentConfiguration;
    
    

    private void Awake()
    {
        if (udpReceiver == null)
        {
            udpReceiver = GetComponent<UdpReceiver>();
        }
    }

    private void OnEnable()
    {
        if (udpReceiver != null)
        {
            udpReceiver.OnMessageReceived += HandleRawMessage;
        }
    }

    private void OnDisable()
    {
        if (udpReceiver != null)
        {
            udpReceiver.OnMessageReceived -= HandleRawMessage;
        }
    }

    private void Update()
    {
        lock (mainThreadActions)
        {
            while (mainThreadActions.Count > 0)
            {
                mainThreadActions.Dequeue()?.Invoke();
            }
        }
    }

    private void HandleRawMessage(string rawJson)
    {
        if (string.IsNullOrWhiteSpace(rawJson))
        {
            return;
        }

        if (logRawMessages)
        {
            Debug.Log("[TrackingIntegrationManager] RAW: " + rawJson);
        }

        MiddlewareEnvelope envelope = null;

        try
        {
            envelope = JsonUtility.FromJson<MiddlewareEnvelope>(rawJson);
        }
        catch (Exception ex)
        {
            Debug.LogWarning("[TrackingIntegrationManager] JSON inválido: " + ex.Message);
            return;
        }

        if (envelope != null && !string.IsNullOrWhiteSpace(envelope.tipo))
        {
            string tipo = envelope.tipo.Trim().ToLowerInvariant();

            switch (tipo)
            {
                case "configuracion":
                    TryHandleConfiguration(rawJson);
                    return;

                case "posicion":
                    TryHandlePosition(rawJson);
                    return;

                default:
                    Debug.LogWarning("[TrackingIntegrationManager] Tipo de mensaje desconocido: " + tipo);
                    return;
            }
        }

        TryHandleHit(rawJson);
    }

    private void TryHandleConfiguration(string rawJson)
    {
        MiddlewareConfigurationMessage message = null;

        try
        {
            message = JsonUtility.FromJson<MiddlewareConfigurationMessage>(rawJson);
        }
        catch (Exception ex)
        {
            Debug.LogWarning("[TrackingIntegrationManager] Error parseando configuración: " + ex.Message);
            return;
        }

        if (message == null || message.elementos == null)
        {
            Debug.LogWarning("[TrackingIntegrationManager] Configuración inválida o vacía.");
            return;
        }

        EnqueueMainThreadAction(() => HandleConfigurationOnMainThread(message));
    }

    private void TryHandlePosition(string rawJson)
    {
        MiddlewarePositionMessage message = null;

        try
        {
            message = JsonUtility.FromJson<MiddlewarePositionMessage>(rawJson);
        }
        catch (Exception ex)
        {
            Debug.LogWarning("[TrackingIntegrationManager] Error parseando posición: " + ex.Message);
            return;
        }

        if (message == null)
        {
            Debug.LogWarning("[TrackingIntegrationManager] Mensaje de posición inválido.");
            return;
        }

        EnqueueMainThreadAction(() => HandlePositionOnMainThread(message));
    }

    private void TryHandleHit(string rawJson)
    {
        MiddlewareHitMessage message = null;

        try
        {
            message = JsonUtility.FromJson<MiddlewareHitMessage>(rawJson);
        }
        catch (Exception ex)
        {
            Debug.LogWarning("[TrackingIntegrationManager] Error parseando hit: " + ex.Message);
            return;
        }

        if (message == null || string.IsNullOrWhiteSpace(message.zone))
        {
            Debug.LogWarning("[TrackingIntegrationManager] Mensaje sin zone válida.");
            return;
        }

        string canonicalZone = NormalizeZoneToCanonical(message.zone);
        string canonicalStick = NormalizeStickToCanonical(message.stick);

        DrumHitData internalHit = new DrumHitData
        {
            zone = canonicalZone,
            stick = canonicalStick,
            timestamp = message.timestamp
        };

        if (logParsedHits)
        {
            Debug.Log("[TrackingIntegrationManager] HIT -> zone=" + internalHit.zone +
                      ", stick=" + internalHit.stick +
                      ", timestamp=" + internalHit.timestamp);
        }

        string rawZoneKey = NormalizeMiddlewareZoneKey(message.zone);

        EnqueueMainThreadAction(() =>
        {
            if (debugOverlay != null && debugOverlay.gameObject.activeInHierarchy)
            {
                debugOverlay.HighlightZone(rawZoneKey);
            }

            if (internalHit.stick == "right" && rightStickVisualizer != null)
            {
                rightStickVisualizer.PulseHitFeedback();
            }
            else if (internalHit.stick == "left" && leftStickVisualizer != null)
            {
                leftStickVisualizer.PulseHitFeedback();
            }
            else if (internalHit.stick == "foot" && footStickVisualizer != null)
            {
                footStickVisualizer.PulseHitFeedback();
            }

            if (drumKitManager != null)
            {
                drumKitManager.PlayHit(internalHit);
            }
            else
            {
                Debug.LogWarning("[TrackingIntegrationManager] DrumKitManager no asignado.");
            }
        });
    }

    private void HandleConfigurationOnMainThread(MiddlewareConfigurationMessage message)
    {
        currentConfiguration = message;

        if (debugOverlay != null)
        {
            debugOverlay.SetConfiguration(message);
        }

        if (drumKitLayoutMapper != null)
        {
            drumKitLayoutMapper.ApplyConfiguration(message);
        }
        else
        {
            Debug.LogWarning("[TrackingIntegrationManager] DrumKitLayoutMapper no asignado.");
        }

        Debug.Log("[TrackingIntegrationManager] Configuracion cargada: " +
                  message.dim_x + "x" + message.dim_y +
                  " | elementos=" + message.elementos.Length);
    }

    private System.Collections.IEnumerator HideOverlayAfterDelay()
    {
        yield return new WaitForSeconds(overlayVisibleSeconds);

        if (debugOverlay != null)
        {
            debugOverlay.gameObject.SetActive(false);
        }

        overlayRoutine = null;
    }

    private void HandlePositionOnMainThread(MiddlewarePositionMessage message)
    {
        if (debugOverlay != null)
        {
            debugOverlay.UpdateStickPosition(1, message.stick_1_x, message.stick_1_y);
            debugOverlay.UpdateStickPosition(2, message.stick_2_x, message.stick_2_y);
        }

        if (!enable3DStickMotion || debugOverlay == null)
        {
            return;
        }

        if (rightStickVisualizer != null)
        {
            if (debugOverlay.TryGetStickViewport(1, out Vector2 rightViewport))
            {
                rightStickVisualizer.SetViewportTrackingPosition(rightViewport);
            }
            else
            {
                rightStickVisualizer.ClearTracking();
            }
        }

        if (leftStickVisualizer != null)
        {
            if (debugOverlay.TryGetStickViewport(2, out Vector2 leftViewport))
            {
                leftStickVisualizer.SetViewportTrackingPosition(leftViewport);
            }
            else
            {
                leftStickVisualizer.ClearTracking();
            }
        }
    }

    private void EnqueueMainThreadAction(Action action)
    {
        lock (mainThreadActions)
        {
            mainThreadActions.Enqueue(action);
        }
    }

    private float NormalizeToUnit(float value, int maxDimension)
    {
        if (maxDimension <= 0)
        {
            return 0f;
        }

        return Mathf.Clamp01(value / maxDimension);
    }

    private string NormalizeZoneToCanonical(string zone)
    {
        string z = NormalizeMiddlewareZoneKey(zone);

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

    private string NormalizeStickToCanonical(int stick)
    {
        switch (stick)
        {
            case 1: return "right";
            case 2: return "left";
            case 3: return "foot";
            default: return "unknown";
        }
    }

    private string NormalizeMiddlewareZoneKey(string zone)
    {
        if (string.IsNullOrWhiteSpace(zone))
        {
            return string.Empty;
        }

        return string.Join(" ", zone.Trim().ToLowerInvariant().Split(' '));
    }
}