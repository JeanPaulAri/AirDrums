using UnityEngine;

public class DrumKitManager : MonoBehaviour
{
    [Header("Pads")]
    [SerializeField] private DrumPad padCrash;
    [SerializeField] private DrumPad padTom;
    [SerializeField] private DrumPad padHiHat;
    [SerializeField] private DrumPad padSnare;
    [SerializeField] private DrumPad padFloorTom;
    [SerializeField] private DrumPad padKick;

    public void PlayHit(DrumHitData hitData)
    {
        if (hitData == null)
        {
            Debug.LogWarning("[DrumKitManager] hitData nulo.");
            return;
        }

        DrumPad targetPad = GetPadByZone(hitData.zone);

        if (targetPad == null)
        {
            Debug.LogWarning($"[DrumKitManager] No se encontro pad para zona '{hitData.zone}'.");
            return;
        }

        targetPad.TriggerHit(hitData.stick);
    }

    private DrumPad GetPadByZone(string zone)
    {
        switch (zone)
        {
            case "crash": return padCrash;
            case "tom": return padTom;
            case "hihat": return padHiHat;
            case "snare": return padSnare;
            case "floor_tom": return padFloorTom;
            case "kick": return padKick;
            default: return null;
        }
    }
}
