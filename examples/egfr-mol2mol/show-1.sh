#!/usr/bin/env bash

num_limit=10

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

           nullif(count(docking_score), 0) AS \"#glide\",
           CASE WHEN min(docking_score) IS NOT NULL
                THEN printf('%6.2f', min(docking_score))
           END AS glide,

           nullif(count(amber_gbsa_score), 0) AS \"#amber\",
           CASE WHEN min(amber_gbsa_score) IS NOT NULL
                THEN printf('%6.2f', min(amber_gbsa_score))
           END AS amber,

           nullif(count(prime_gbsa_score), 0) AS \"#prime\",
           CASE WHEN min(prime_gbsa_score) IS NOT NULL
                THEN printf('%6.2f', min(prime_gbsa_score))
           END AS prime,

           strftime('%H:%M', min(modified_at), '+9 hours') AS modified
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
    split("5 5 6 4 6 6 6 6 6 6 8", w, " ")
    split("cycle count mw rot #glide glide #amber amber #prime prime modified", h, " ")

    border("╭", "┬", "╮", "─")

    # 헤더: 가운데 정렬
    printf "│"
    for (i = 1; i <= 11; i++) {
        left = int((w[i] - length(h[i])) / 2)
        printf "%*s%s%*s│", left, "", h[i], w[i] - length(h[i]) - left, ""
    }
    printf "\n"

    border("╞", "╪", "╡", "═")
}
{
    printf "│"

    for (i = 1; i <= 11; i++) {
        value = $i

        # 너비를 초과하는 값은 별표로 표시
        if (length(value) > w[i]) {
            value = sprintf("%*s", w[i], "")
            gsub(/ /, "*", value)
        }

        if (i == 11) {
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

    for (i = 1; i <= 11; i++) {
        for (j = 1; j <= w[i]; j++) {
            printf "%s", ch
        }
        printf "%s", (i < 11 ? middle : last)
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
        sleep 5s
    fi

    echo
    echo
done

