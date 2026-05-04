using System.Collections;
using UnityEngine;

public class DrumPad : MonoBehaviour
{
    [Header("Identity")]
    [SerializeField] private string zoneName;

    [Header("Visual")]
    [SerializeField] private Transform visualTarget;
    [SerializeField] private Renderer visualRenderer;
    [SerializeField] private bool useAutoZoneBaseColor = true;
    [SerializeField] private Color baseColorOverride = Color.white;
    [SerializeField] private Color hitColor = Color.yellow;
    [SerializeField] private float hitDuration = 0.12f;
    [SerializeField] private float hitScaleMultiplier = 1.12f;

    private Vector3 initialScale;
    private Color initialColor;
    private Coroutine hitRoutine;

    public string ZoneName => zoneName;

    private void Awake()
    {
        if (visualTarget == null && transform.childCount > 0)
        {
            visualTarget = transform.GetChild(0);
        }

        if (visualRenderer == null && visualTarget != null)
        {
            visualRenderer = visualTarget.GetComponent<Renderer>();
        }

        if (visualTarget != null)
        {
            initialScale = visualTarget.localScale;
        }

        if (visualRenderer != null)
        {
            Color baseColor = useAutoZoneBaseColor ? GetBaseColorByZone(zoneName) : baseColorOverride;
            visualRenderer.material.color = baseColor;
            initialColor = baseColor;

            // Para tarola amarilla, conviene que el hit contraste más.
            if (NormalizeZone(zoneName) == "snare" && hitColor == Color.yellow)
            {
                hitColor = Color.white;
            }
        }
    }

    public void TriggerHit(string stick = "unknown")
    {
        Debug.Log("[DrumPad] Golpe en " + zoneName + " con " + stick);

        if (hitRoutine != null)
        {
            StopCoroutine(hitRoutine);
        }

        hitRoutine = StartCoroutine(HitFeedbackRoutine());
    }

    private IEnumerator HitFeedbackRoutine()
    {
        if (visualTarget != null)
        {
            visualTarget.localScale = initialScale * hitScaleMultiplier;
        }

        if (visualRenderer != null)
        {
            visualRenderer.material.color = hitColor;
        }

        yield return new WaitForSeconds(hitDuration);

        if (visualTarget != null)
        {
            visualTarget.localScale = initialScale;
        }

        if (visualRenderer != null)
        {
            visualRenderer.material.color = initialColor;
        }

        hitRoutine = null;
    }

    private string NormalizeZone(string zone)
    {
        if (string.IsNullOrWhiteSpace(zone))
        {
            return string.Empty;
        }

        return zone.Trim().ToLowerInvariant();
    }

    private Color GetBaseColorByZone(string zone)
    {
        switch (NormalizeZone(zone))
        {
            case "crash":
                return Color.green;

            case "hihat":
                return Color.red;

            case "snare":
                return new Color(1f, 0.85f, 0f); // amarillo más usable

            case "tom":
                return Color.blue;

            case "floor_tom":
                return new Color(1f, 0.5f, 0f); // naranja

            case "kick":
                return new Color(0.6f, 0.2f, 0.8f); // morado

            default:
                return Color.white;
        }
    }
}