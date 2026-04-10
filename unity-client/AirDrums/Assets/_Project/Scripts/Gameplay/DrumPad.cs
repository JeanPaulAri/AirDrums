using System.Collections;
using UnityEngine;

public class DrumPad : MonoBehaviour
{
    [Header("Identity")]
    [SerializeField] private string zoneName;

    [Header("Visual")]
    [SerializeField] private Transform visualTarget;
    [SerializeField] private Renderer visualRenderer;
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
            initialColor = visualRenderer.material.color;
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
}