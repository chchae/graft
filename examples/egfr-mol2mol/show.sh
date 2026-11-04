#!/usr/bin/env bash

num_limit=5

while true; do
    echo "----------------------------------------------------------------------------"
    date

    if [ -f output/egfr.sqlite ] &&
       [ "$(sqlite3 output/egfr.sqlite \
           "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='compound';" \
           2>/dev/null)" = "1" ]; then

        sqlite3 -batch -noheader -separator '|' -nullvalue '' \
            output/egfr.sqlite "
SELECT *
FROM (
    SELECT iteration AS cycle,
           count(*) AS count,

           CASE WHEN avg(rdk_mw) IS NOT NULL
                THEN printf('%6.1f', avg(rdk_mw))
           END AS mw,

           CASE WHEN avg(rdk_rotatable_bonds) IS NOT NULL
                THEN printf('%4.1f', avg(rdk_rotatable_bonds))
           END AS rot,

           nullif(count(docking_score), 0) AS \"#glid\",
           CASE WHEN min(docking_score) IS NOT NULL
                THEN printf('%6.2f', min(docking_score))
           END AS glide,

           nullif(count(amber_gbsa_score), 0) AS \"#ambe\",
           CASE WHEN min(amber_gbsa_score) IS NOT NULL
                THEN printf('%6.2f', min(amber_gbsa_score))
           END AS amber,

           nullif(count(prime_gbsa_score), 0) AS \"#prim\",
           CASE WHEN min(prime_gbsa_score) IS NOT NULL
                THEN printf('%6.2f', min(prime_gbsa_score))
           END AS prime,

           nullif(count(md_gbsa_score), 0) AS \"#md\",
           CASE WHEN min(md_gbsa_score) IS NOT NULL
                THEN printf('%6.2f', min(md_gbsa_score))
           END AS md,

           strftime('%H:%M', min(modified_at), '+9 hours') AS modif
    FROM compound
    GROUP BY iteration
    ORDER BY cycle DESC
    LIMIT $num_limit
)
ORDER BY cycle;
" | awk '
BEGIN {
    FS = "|"

    # 각 열의 너비: cycle ~ modified
    split("5 5 6 4 5 6 5 7 5 7 5 7 6", w, " ")
    split("cycle count mw rot #glid glide #ambe amber #prim prime #md md modif", h, " ")

    border("╭", "┬", "╮", "─")

    # 헤더: 가운데 정렬
    printf "│"
    for (i = 1; i <= 13; i++) {
        left = int((w[i] - length(h[i])) / 2)
        printf "%*s%s%*s│", left, "", h[i], w[i] - length(h[i]) - left, ""
    }
    printf "\n"

    border("╞", "╪", "╡", "═")
}
{
    printf "│"

    for (i = 1; i <= 13; i++) {
        value = $i

        # 너비를 초과하는 값은 별표로 표시
        if (length(value) > w[i]) {
            value = sprintf("%*s", w[i], "")
            gsub(/ /, "*", value)
        }

        if (i == 13) {
            # modified: 왼쪽 정렬
            printf "%-*s│", w[i], value
        } else {
            # 숫자: 오른쪽 정렬
            printf "%*s│", w[i], value
        }
    }

    printf "\n"
}
END {
    border("╰", "┴", "╯", "─")
}
function border(first, middle, last, ch,    i, j) {
    printf "%s", first

    for (i = 1; i <= 13; i++) {
        for (j = 1; j <= w[i]; j++) {
            printf "%s", ch
        }
        printf "%s", (i < 13 ? middle : last)
    }

    printf "\n"
}'

        # ssh -n slurm squeue; echo

        njobs_slurm=$(ssh -n slurm squeue | grep -v JOBID | wc -l)
        njobs_t41=$(ssh -n t41 squeue | grep -v JOBID | wc -l)

        printf 'Num_Jobs:  slurm=%s  t41=%s\n\n' \
            "$njobs_slurm" "$njobs_t41"

        ssh -n plaza licstatus.run

        break
    fi

    if IFS= read -r -s -n 1 -t 1 key; then
        case "$key" in
            " "|"")
                num_limit=10000
                continue
                ;;
        esac
    else
        num_limit=10
        sleep 10s
    fi

done

